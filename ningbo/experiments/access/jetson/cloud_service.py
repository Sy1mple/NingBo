"""复用 FaceService 的线程、停止、会话及结果协议，云端执行检测和逐人比对。"""
import time
from experiments.access.jetson.xfyun_client import XfyunFaceClient


def capture(service, client, camera, cancel):
    import cv2
    frame = camera.read()
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError('摄像头须返回 BGR 彩色图像')
    height, width = frame.shape[:2]
    if min(height, width) <= 0:
        raise ValueError('摄像头图像为空')
    scale = min(1.0, 1280 / max(height, width))
    if scale < 1:
        frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
    ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError('摄像头 JPEG 编码失败')
    jpeg = encoded.tobytes()
    result = client.detect(jpeg, cancel)
    count = result['face_num']
    if count != 1:
        return None, '请保持一人在画面中' if count else '等待人脸进入画面', count
    face = result.get('face_1', {})
    try:
        x, y, w, h = (face[k] for k in ('x', 'y', 'w', 'h'))
        if any(type(v) is not int for v in (x, y, w, h)) or x < 0 or y < 0 or w <= 0 or h <= 0:
            raise ValueError()
        if x + w > frame.shape[1] or y + h > frame.shape[0]:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('讯飞人脸位置字段无效') from None
    if min(w, h) < service.config.minimum_face_pixels:
        return None, '人脸太小，请靠近摄像头', count
    gray = cv2.cvtColor(frame[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < service.config.minimum_sharpness:
        return None, '人脸画面模糊，请保持稳定', count
    return jpeg, '有效人脸', count


def match(client, jpeg, gallery, config, cancel):
    candidates = []
    for person in gallery:
        if cancel.is_set():
            raise InterruptedError('人脸任务已停止')
        score = client.compare(jpeg, person['jpeg'], cancel)
        candidates.append({'person_id': person['person_id'], 'name': person['name'], 'score': score})
    candidates.sort(key=lambda p: p['score'], reverse=True)
    if not candidates:
        return {'recognized': False, 'person_id': None, 'name': None, 'score': None, 'note': '人脸库为空，请先录入'}
    best = candidates[0]
    separated = len(candidates) == 1 or best['score'] - candidates[1]['score'] >= config.cloud_margin
    recognized = best['score'] >= config.cloud_threshold and separated
    return {**best, 'recognized': recognized, 'note': '已匹配库内人员' if recognized else
            '候选人员相似度接近，拒绝确认' if not separated else '未匹配到库内人员'}


def run_cloud_task(service, mode, session_id, generation, cancel):
    camera, error = None, ''
    try:
        client = service.cloud_client_factory(service.config)
        service.library.initialize()
        gallery = service.library.samples()
        if mode == 'recognize' and not gallery:
            service.complete_recognition(generation, session_id, dict(recognized=False,
                          person_id=None, name=None, score=None, note='人脸库为空，请先录入'))
            return
        if cancel.is_set():
            return
        camera = service.camera_for_task()
        service.start_camera(camera, cancel)
        with service._lock:
            if generation == service._generation:
                service._state['phase'] = 'enrolling' if mode == 'enroll' else 'recognizing'
        if mode == 'enroll' and not service.prepare_enrollment(cancel):
            return
        deadline = time.monotonic() + (service.config.enroll_timeout if mode == 'enroll' else service.config.verification_timeout)
        candidate, streak = None, 0
        while not cancel.is_set():
            if mode == 'recognize' and time.monotonic() >= deadline:
                service.complete_recognition(generation, session_id, dict(recognized=False,
                    person_id=None, name=None, score=None, note='身份验证超时，请重新点击验证'))
                return
            if mode == 'enroll' and time.monotonic() >= deadline:
                raise TimeoutError('人脸录入超时，请保持画面清晰且只有一人')
            if mode == 'recognize':
                service._emit(generation, 'recognition', session_id=session_id, recognized=None,
                              person_id=None, name=None, score=None, note='正在请求讯飞人脸比对')
            jpeg, note, count = capture(service, client, camera, cancel)
            with service._lock:
                if generation == service._generation and not cancel.is_set():
                    service._state['note'] = note
            if jpeg is not None:
                result = match(client, jpeg, gallery, service.config, cancel)
                if mode == 'enroll':
                    if gallery and result['score'] >= service.config.cloud_threshold and not result['recognized']:
                        raise ValueError('录入对象与多个已有人员相似，请核对人员库后重新录入')
                    with service._lock:
                        if generation != service._generation or cancel.is_set():
                            return
                        if time.monotonic() >= deadline:
                            raise TimeoutError('人脸录入超时，请重试或减少人员库规模')
                        if result['recognized']:
                            person = {k: result[k] for k in ('person_id', 'name')}
                            person['duplicate'] = True
                            note = '该人员已在人脸库中，无需重复录入'
                        else:
                            person = service.library.add_photo(jpeg, service._enroll_name)
                            service._enroll_name = ''
                            note = '人脸录入完成（讯飞照片库）'
                        service._state.update(person=person, samples=1, note=note)
                        service._emit(generation, 'enrollment', success=True, person=person, note=note)
                    return
                if result['recognized']:
                    streak = streak + 1 if candidate == result['person_id'] else 1
                    candidate = result['person_id']
                    if streak < service.config.cloud_confirmations:
                        result.update(recognized=None, note='正在连续确认人员')
                else:
                    candidate, streak = None, 0
            else:
                candidate, streak = None, 0
                result = {'recognized': False if count > 1 else None, 'person_id': None,
                          'name': None, 'score': None, 'note': note}
            if mode == 'recognize':
                if time.monotonic() >= deadline:
                    result = dict(recognized=False, person_id=None, name=None, score=None,
                                  note='身份验证超时，请重新点击验证')
                if isinstance(result['recognized'], bool):
                    service.complete_recognition(generation, session_id, result)
                    return
                service._emit(generation, 'recognition', session_id=session_id, **result)
            if cancel.wait(service.config.cloud_round_interval):
                return
    except InterruptedError:
        if not cancel.is_set():
            error = '人脸请求意外中断'
    except Exception as exc:
        error = str(exc)
    finally:
        if camera is not None:
            try:
                camera.close()
            except Exception as exc:
                error = error or str(exc)
        with service._lock:
            if generation == service._generation:
                service._state.update(phase='error' if error else 'completed', error=error)

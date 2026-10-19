"""讯飞听写：流式收发、动态修正、持久化免费调用上限。"""
import base64
from collections import deque
from contextlib import closing
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
import hashlib
import hmac
import json
import logging
import os
import queue
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlencode, urlsplit

LOGGER = logging.getLogger(__name__)


def credentials(config):
    values = {}
    path = Path(config.xfyun_credentials_file)
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key in ('XFYUN_APP_ID', 'XFYUN_API_KEY', 'XFYUN_API_SECRET'):
                values[key] = value.strip().strip('"\'')
    for key in ('XFYUN_APP_ID', 'XFYUN_API_KEY', 'XFYUN_API_SECRET'):
        values[key] = os.environ.get(key, values.get(key, '')).strip()
    if not all(values.values()):
        raise RuntimeError('未配置讯飞密钥，请在项目 .env.voice 填写 XFYUN_APP_ID、XFYUN_API_KEY、XFYUN_API_SECRET')
    return values


def signed_url(endpoint, api_key, secret, now=None):
    url = urlsplit(endpoint)
    if endpoint != 'wss://iat-api.xfyun.cn/v2/iat':
        raise ValueError('只允许讯飞官方 WSS 接口')
    date = format_datetime(now or datetime.now(timezone.utc), usegmt=True)
    content = f'host: {url.netloc}\ndate: {date}\nGET {url.path} HTTP/1.1'
    signature = base64.b64encode(hmac.new(secret.encode(), content.encode(), hashlib.sha256).digest()).decode()
    auth = f'api_key="{api_key}",algorithm="hmac-sha256",headers="host date request-line",signature="{signature}"'
    return endpoint + '?' + urlencode({'host': url.netloc, 'date': date, 'authorization': base64.b64encode(auth.encode()).decode()})


class DailyQuota:
    def __init__(self, path, app_id, limit, clock=None):
        self.path, self.app = Path(path), hashlib.sha256(app_id.encode()).hexdigest()
        self.limit = limit
        self.clock = clock or (lambda: datetime.now(timezone(timedelta(hours=8))))

    def reserve(self):
        date = self.clock()
        if date.year < 2025:
            raise RuntimeError('开发板时间尚未校准，请先同步系统时间')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.path), timeout=2)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (day TEXT, app TEXT, used INTEGER, PRIMARY KEY(day,app))')
            db.execute('BEGIN IMMEDIATE')
            day = date.date().isoformat()
            row = db.execute('SELECT used FROM calls WHERE day=? AND app=?', (day, self.app)).fetchone()
            used = row[0] if row else 0
            if used >= self.limit:
                raise RuntimeError(f'讯飞今日已达到本项目 {self.limit} 次上限，停止调用；不会切换收费接口')
            db.execute('INSERT OR REPLACE INTO calls VALUES (?,?,?)', (day, self.app, used + 1))
        return used + 1


class PcmEncoder:
    """AIMIC-M4 48k→16k 连续滤波，保留块间滤波状态和抽样相位。"""
    def __init__(self, rate, gain):
        import numpy as np
        if rate < 16000 or rate % 16000:
            raise ValueError('讯飞当前采音支持 16k 的整数倍采样率；AIMIC-M4 使用 48k')
        self.factor, self.gain = rate // 16000, gain
        self.seen = 0
        self.sos = None
        if self.factor > 1:
            from scipy.signal import butter
            self.sos = butter(8, .9 / self.factor, output='sos')
            self.state = np.zeros((len(self.sos), 2))

    def convert(self, pcm):
        import numpy as np
        if len(pcm) % 2:
            raise ValueError('PCM 必须为完整 int16 样本')
        x = np.frombuffer(pcm, dtype='<i2').astype(np.float64) * self.gain / 32768
        if self.sos is not None and len(x):
            from scipy.signal import sosfilt
            x, self.state = sosfilt(self.sos, x, zi=self.state)
        start = (-self.seen) % self.factor
        self.seen += len(x)
        y = np.clip(x[start::self.factor], -1, 1)
        return (np.rint(y * 32767).astype('<i2').tobytes(), float(np.sqrt(np.mean(y*y))) if y.size else 0)


class XfyunTransportError(RuntimeError):
    """可重建连接的传输故障；不包含签名 URL 或底层异常内容。"""


class XfyunServiceError(RuntimeError):
    """授权、额度或返回格式错误，禁止自动重试消耗调用次数。"""


class ResultAssembler:
    def __init__(self):
        self.parts = {}

    def accept(self, message):
        if message.get('code') != 0:
            raise XfyunServiceError(f"讯飞识别返回错误码 {message.get('code')}；请检查授权和免费额度")
        data = message.get('data', {})
        result = data.get('result')
        final = data.get('status') == 2
        if result:
            if result.get('pgs') == 'rpl':
                first, last = result['rg']
                for key in list(self.parts):
                    if first <= key <= last:
                        self.parts.pop(key)
            self.parts[result['sn']] = ''.join(w['cw'][0]['w'] for w in result.get('ws', []) if w.get('cw'))
        return {'text': ''.join(self.parts[key] for key in sorted(self.parts)), 'final': final}


class XfyunRecognizer:
    MAX_RECONNECT_ATTEMPTS = 3

    def __init__(self, config, sample_rate, module=None, quota=None):
        self.config = config
        self.keys = credentials(config)
        if module is None:
            import websocket as module
        self.module = module
        self.sample_rate = sample_rate
        self.encoder = PcmEncoder(sample_rate, config.input_gain)
        self.quota = quota or DailyQuota(config.xfyun_usage_file, self.keys['XFYUN_APP_ID'], config.xfyun_daily_limit)
        self.lock = threading.RLock()
        self.results = deque(maxlen=config.result_queue_size)
        self.ws = None
        self.reader = None
        self.done = threading.Event()
        self.closing = threading.Event()
        self.error = None
        self.pending = deque(maxlen=8)
        self.trigger_samples = 0
        self.sent_samples = 0
        self.silent_samples = 0
        self.next_send = 0
        self._stop = threading.Event()
        self._reset_audio = threading.Event()
        self._audio = queue.Queue(maxsize=config.audio_queue_blocks)
        self._fatal = None
        self._phase, self._notice = 'listening', '正在聆听，请说话'
        self._failures = 0
        self._retry_at = 0
        self.dropped_audio_blocks = 0
        self.worker = threading.Thread(target=self._run, name='xfyun-audio', daemon=True)
        self.worker.start()

    def _status(self, phase, notice):
        with self.lock:
            self._phase, self._notice = phase, notice

    def snapshot(self):
        with self.lock:
            return {'phase': self._phase, 'notice': self._notice,
                    'dropped_audio_blocks': self.dropped_audio_blocks}

    def process(self, pcm):
        """采音线程只投递音频和读取结果；连接、限速和句末等待在独立线程执行。"""
        with self.lock:
            if self._fatal is not None:
                raise self._fatal
        if not self._stop.is_set():
            try:
                self._audio.put_nowait(pcm)
            except queue.Full:
                self.reset_audio()
        with self.lock:
            if self._fatal is not None:
                raise self._fatal
            return self.results.popleft() if self.results else {'text': '', 'final': False}

    def reset_audio(self):
        """积压时撤销当前句，跳过旧音频，继续等新讲话；不拼接断裂的音频。"""
        count = 1
        while True:
            try:
                self._audio.get_nowait()
                count += 1
            except queue.Empty:
                break
        with self.lock:
            self.dropped_audio_blocks += count
        self._reset_audio.set()
        self._status('recovering', '音频积压已清理，请重新说话')

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    if self._reset_audio.is_set():
                        self._reset_audio.clear()
                        self._drop_session()
                        self.encoder = PcmEncoder(self.sample_rate, self.config.input_gain)
                        self._status('recovering', '音频积压已清理，请重新说话')
                    self._check_completed()
                    try:
                        pcm = self._audio.get(timeout=.05)
                    except queue.Empty:
                        continue
                    if pcm is not None and not self._stop.is_set():
                        self._process_audio(pcm)
                except XfyunTransportError as exc:
                    self._drop_session()
                    self._failures += 1
                    if self._failures > self.MAX_RECONNECT_ATTEMPTS:
                        raise RuntimeError('语音连接连续失败，请检查网络后重新点开始') from None
                    self._retry_at = time.monotonic() + min(2 ** (self._failures - 1), 4)
                    self._status('recovering', '语音连接中断，正在恢复')
                    LOGGER.warning('语音传输中断，第 %d/%d 次恢复：%s',
                                   self._failures, self.MAX_RECONNECT_ATTEMPTS, exc)
        except InterruptedError:
            pass
        except Exception as exc:
            with self.lock:
                self._fatal = exc
            self._status('error', str(exc))
        finally:
            try:
                self._drop_session()
            except Exception:
                with self.lock:
                    self._fatal = self._fatal or RuntimeError('语音连接清理失败，请重新点开始')

    def _check_completed(self):
        with self.lock:
            error = self.error
        if error is not None:
            raise error
        if self.ws is not None and self.done.is_set():
            self._drop_session()
            self._failures = 0
            self._status('listening', '正在聆听，请继续说话')

    def _open(self):
        self._status('connecting', '正在连接语音服务')
        used = self.quota.reserve()  # 连接失败也计数，保守保留预算。
        try:
            self.ws = self.module.create_connection(signed_url(self.config.xfyun_endpoint,
                self.keys['XFYUN_API_KEY'], self.keys['XFYUN_API_SECRET']),
                timeout=self.config.xfyun_connect_timeout, enable_multithread=True)
            self.ws.settimeout(1.0)
        except Exception:
            if self.ws is not None:
                self.ws.close()
                self.ws = None
            raise XfyunTransportError('讯飞连接失败') from None
        if self._stop.is_set():
            raise InterruptedError()
        self.done.clear()
        self.closing.clear()
        self.assembler = ResultAssembler()
        self.error = None
        self.sent_samples = self.silent_samples = 0
        self.next_send = 0
        self.reader = threading.Thread(target=self._receive,
            args=(self.ws, self.done, self.closing, self.assembler), name='xfyun-result', daemon=True)
        self.reader.start()
        self._status('listening', '正在识别，请说话')
        LOGGER.info('讯飞流式会话已连接，今日本项目调用 %s/%s', used, self.config.xfyun_daily_limit)

    def _receive(self, ws, done, closing, assembler):
        try:
            while not closing.is_set():
                try:
                    data = ws.recv()
                except self.module.WebSocketTimeoutException:
                    continue
                if not data:
                    if not done.is_set():
                        raise XfyunTransportError('讯飞连接提前关闭')
                    return
                result = assembler.accept(json.loads(data))
                with self.lock:
                    if result['text']:
                        self.results.append(result)
                if result['final']:
                    done.set()
                    return
        except Exception as exc:
            if not closing.is_set() and not self._stop.is_set():
                with self.lock:
                    # 不记录签名 URL、APIKey 或原始网络异常。
                    if isinstance(exc, (XfyunServiceError, XfyunTransportError)):
                        self.error = exc
                    elif isinstance(exc, (ValueError, KeyError, TypeError)):
                        self.error = XfyunServiceError('讯飞返回格式异常')
                    else:
                        self.error = XfyunTransportError('讯飞接收连接异常')
                done.set()

    def _send(self, pcm):
        first = self.sent_samples == 0
        msg = {'data': {'status': 0 if first else 1, 'format': 'audio/L16;rate=16000',
                       'encoding': 'raw', 'audio': base64.b64encode(pcm).decode()}}
        if first:
            msg.update(common={'app_id': self.keys['XFYUN_APP_ID']},
                       business={'language': 'zh_cn', 'domain': 'iat', 'accent': 'mandarin',
                                 'dwa': 'wpgs', 'rlang': 'zh-cn', 'eos': 2000})
        while self.next_send > time.monotonic() and not self.done.is_set():
            if self._stop.wait(max(0, min(.02, self.next_send - time.monotonic()))):
                raise InterruptedError()
        if self._stop.is_set():
            raise InterruptedError()
        if self.done.is_set():
            self._check_completed()
            return False
        try:
            self.ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception:
            # 句末回包可能与发送同时发生，先核对正常结束或平台明确错误。
            if self.done.wait(.2):
                self._check_completed()
                return False
            raise XfyunTransportError('讯飞音频发送超时或连接中断') from None
        self.next_send = max(self.next_send, time.monotonic() - .08) + len(pcm) / 32000
        self.sent_samples += len(pcm) // 2
        return True

    def _finish(self):
        try:
            if not self.done.is_set():
                try:
                    self.ws.send(json.dumps({'data': {'status': 2}}))
                except Exception:
                    if not self.done.wait(.2):
                        raise XfyunTransportError('讯飞结束帧发送失败') from None
                deadline = time.monotonic() + self.config.xfyun_final_timeout
                while not self.done.wait(.05):
                    if self._stop.is_set():
                        raise InterruptedError()
                    if time.monotonic() >= deadline:
                        raise XfyunTransportError('讯飞句末结果返回超时')
            with self.lock:
                if self.error is not None:
                    raise self.error
            self._failures = 0
            self._status('listening', '正在聆听，请继续说话')
        finally:
            self._drop_session()

    def _drop_session(self):
        self.closing.set()
        if self.ws is not None:
            self.ws.close(timeout=.2)
        if self.reader:
            self.reader.join(1.2)
            if self.reader.is_alive():
                raise RuntimeError('讯飞接收线程未结束')
        self.ws = None
        self.reader = None
        self.error = None
        self.pending.clear()
        self.trigger_samples = 0

    def _process_audio(self, pcm):
        if time.monotonic() < self._retry_at:
            return
        converted, rms = self.encoder.convert(pcm)
        if not converted:
            return
        voiced = rms >= self.config.silence_rms
        if self.ws is None:
            self.pending.append(converted)
            self.trigger_samples = self.trigger_samples + len(converted)//2 if voiced else 0
            if self.trigger_samples >= self.config.xfyun_trigger_seconds * 16000:
                self._open()
                for chunk in tuple(self.pending):
                    if not self._send(chunk):
                        break
                self.pending.clear()
        else:
            if not self._send(converted):
                return
            self.silent_samples = 0 if voiced else self.silent_samples + len(converted)//2
            if (self.silent_samples >= self.config.silence_seconds * 16000
                    or self.sent_samples >= self.config.xfyun_max_seconds * 16000):
                self._finish()

    def close(self):
        # 停止按钮取消当前会话，停止后不回写尾部结果。
        self._stop.set()
        ws = self.ws
        try:
            if ws is not None:
                ws.close(timeout=.2)
        finally:
            self.worker.join(4.0)
        if self.worker.is_alive():
            raise RuntimeError('语音网络线程未结束，暂时禁止再次启动')

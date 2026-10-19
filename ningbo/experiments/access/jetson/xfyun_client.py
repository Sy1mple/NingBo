"""讯飞自研人脸检测/比对 HTTPS API；试用期限与总次数由控制台据实配置。"""
import base64
from contextlib import closing
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

ENDPOINT = 'https://api.xf-yun.com/v1/private/s67c9c78c'
KEYS = ('XFYUN_FACE_APP_ID', 'XFYUN_FACE_API_KEY', 'XFYUN_FACE_API_SECRET',
        'XFYUN_FACE_DETECT_LIMIT', 'XFYUN_FACE_COMPARE_LIMIT', 'XFYUN_FACE_FREE_EXPIRES')


def trial_expiry(value):
    # 控制台明确显示“-”时保留未知日期，仍执行持久次数上限和平台授权检查。
    if value == '-':
        return None
    try:
        expiry = datetime.fromisoformat(value)
        if expiry.tzinfo is None:
            raise ValueError()
        return expiry
    except ValueError:
        raise ValueError('免费到期时间须带时区；仅当控制台未显示日期时可填 -') from None


def credentials(config):
    values = {}
    path = Path(config.credentials_file)
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key in KEYS:
                values[key] = value.strip().strip('\"\'')
    for key in KEYS:
        values[key] = os.environ.get(key, values.get(key, '')).strip()
    if not all(values.values()):
        raise RuntimeError('请先运行 python deploy/configure_face.py，配置讯飞人脸专用密钥、免费次数和到期时间')
    for key in ('XFYUN_FACE_DETECT_LIMIT', 'XFYUN_FACE_COMPARE_LIMIT'):
        if not values[key].isdecimal() or int(values[key]) <= 0:
            raise ValueError('人脸检测和比对免费次数须为控制台剩余免费次数的正整数')
    trial_expiry(values['XFYUN_FACE_FREE_EXPIRES'])
    return values


def signed_url(api_key, secret, now=None):
    date = format_datetime(now or datetime.now(timezone.utc), usegmt=True)
    source = 'host: api.xf-yun.com\ndate: ' + date + '\nPOST /v1/private/s67c9c78c HTTP/1.1'
    signature = base64.b64encode(hmac.new(secret.encode(), source.encode(), hashlib.sha256).digest()).decode()
    auth = f'api_key="{api_key}",algorithm="hmac-sha256",headers="host date request-line",signature="{signature}"'
    return ENDPOINT + '?' + urlencode({'host': 'api.xf-yun.com', 'date': date,
        'authorization': base64.b64encode(auth.encode()).decode()})


class TrialQuota:
    """按 APPID 和能力持久计数；失败请求也计数，日期变化不会重置。"""
    def __init__(self, path, keys, clock=None):
        self.path = Path(path)
        self.app = hashlib.sha256(keys['XFYUN_FACE_APP_ID'].encode()).hexdigest()
        self.expiry = trial_expiry(keys['XFYUN_FACE_FREE_EXPIRES'])
        self.limits = {'face_detect': int(keys['XFYUN_FACE_DETECT_LIMIT']),
                       'face_compare': int(keys['XFYUN_FACE_COMPARE_LIMIT'])}
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def reserve(self, kind):
        now = self.clock()
        if now.year < 2025 or (self.expiry is not None and now >= self.expiry):
            raise RuntimeError('人脸免费试用已到期或系统时间未校准，已停止调用')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.path), timeout=2)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (app TEXT, kind TEXT, used INTEGER, PRIMARY KEY(app,kind))')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT used FROM calls WHERE app=? AND kind=?', (self.app, kind)).fetchone()
            used = row[0] if row else 0
            if used >= self.limits[kind]:
                raise RuntimeError('人脸' + ('检测' if kind == 'face_detect' else '比对') + '免费调用已达本机上限，已停止调用')
            db.execute('INSERT OR REPLACE INTO calls VALUES (?,?,?)', (self.app, kind, used + 1))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class XfyunFaceClient:
    def __init__(self, config, opener=None, quota=None):
        self.config = config
        self.keys = credentials(config)
        self.quota = quota or TrialQuota(config.usage_file, self.keys)
        self.opener = opener or build_opener(NoRedirect())
        self.next_call = 0.0

    def call(self, kind, images, cancel):
        if kind not in ('face_detect', 'face_compare') or len(images) != (1 if kind == 'face_detect' else 2):
            raise ValueError('人脸请求类型或图片数量无效')
        if any(not isinstance(p, bytes) or not p or len(base64.b64encode(p)) > 4 * 1024 * 1024 for p in images):
            raise ValueError('人脸照片须为非空 JPEG，Base64 编码后每张不超过 4MB')
        if cancel.wait(max(0, self.next_call - time.monotonic())):
            raise InterruptedError('人脸任务已停止')
        result_key = kind + '_result'
        parameters = {'service_kind': kind, result_key: {'encoding': 'utf8', 'compress': 'raw', 'format': 'json'}}
        if kind == 'face_detect':
            parameters.update(detect_points='0', detect_property='0')
        body = {'header': {'app_id': self.keys['XFYUN_FACE_APP_ID'], 'status': 3},
                'parameter': {'s67c9c78c': parameters},
                'payload': {f'input{i}': {'encoding': 'jpg', 'status': 3, 'image': base64.b64encode(p).decode()}
                            for i, p in enumerate(images, 1)}}
        if cancel.is_set():
            raise InterruptedError('人脸任务已停止')
        self.quota.reserve(kind)
        self.next_call = time.monotonic() + self.config.api_interval
        request = Request(signed_url(self.keys['XFYUN_FACE_API_KEY'], self.keys['XFYUN_FACE_API_SECRET']),
                          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self.opener.open(request, timeout=self.config.api_timeout) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError()
            data = json.loads(raw)
        except HTTPError as exc:
            raise RuntimeError(f'讯飞人脸 HTTP {exc.code}，请检查人脸能力授权、密钥和免费额度') from None
        except Exception:
            raise RuntimeError('讯飞人脸请求失败，请检查网络、系统时间及服务状态') from None
        if cancel.is_set():
            raise InterruptedError('人脸任务已停止')
        try:
            code = data['header']['code']
            if type(code) is not int or code != 0:
                raise RuntimeError('讯飞人脸平台返回失败，请检查授权和免费额度')
            result = json.loads(base64.b64decode(data['payload'][result_key]['text'], validate=True))
            if type(result['ret']) is not int or result['ret'] != 0:
                raise RuntimeError('讯飞人脸业务返回失败，请检查照片、授权和免费额度')
            return result
        except (KeyError, TypeError, ValueError):
            raise RuntimeError('讯飞人脸返回数据不完整或格式错误，拒绝作为识别成功') from None

    def detect(self, jpeg, cancel):
        result = self.call('face_detect', [jpeg], cancel)
        count = result.get('face_num')
        if type(count) is not int or count < 0:
            raise RuntimeError('讯飞人脸数量字段无效')
        return result

    def compare(self, first, second, cancel):
        result = self.call('face_compare', [first, second], cancel)
        score = result.get('score')
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise RuntimeError('讯飞人脸相似度字段无效')
        return float(score)

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


class ResultAssembler:
    def __init__(self):
        self.parts = {}

    def accept(self, message):
        if message.get('code') != 0:
            raise RuntimeError(f"讯飞识别返回错误码 {message.get('code')}；请检查授权和免费额度")
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
    def __init__(self, config, sample_rate, module=None, quota=None):
        self.config = config
        self.keys = credentials(config)
        if module is None:
            import websocket as module
        self.module = module
        self.encoder = PcmEncoder(sample_rate, config.input_gain)
        self.quota = quota or DailyQuota(config.xfyun_usage_file, self.keys['XFYUN_APP_ID'], config.xfyun_daily_limit)
        self.lock = threading.RLock()
        self.results = deque(maxlen=config.result_queue_size)
        self.ws = None
        self.reader = None
        self.done = threading.Event()
        self.closing = threading.Event()
        self.error = ''
        self.pending = deque(maxlen=8)
        self.trigger_samples = 0
        self.sent_samples = 0
        self.silent_samples = 0
        self.next_send = 0

    def _open(self):
        used = self.quota.reserve()  # 连接失败也计数，保守保留预算。
        try:
            self.ws = self.module.create_connection(signed_url(self.config.xfyun_endpoint,
                self.keys['XFYUN_API_KEY'], self.keys['XFYUN_API_SECRET']),
                timeout=self.config.xfyun_connect_timeout, enable_multithread=True)
            self.ws.settimeout(.2)
        except Exception:
            if self.ws is not None:
                self.ws.close()
                self.ws = None
            raise RuntimeError('讯飞连接失败，请检查网络、系统时间及密钥；本次没有降级到其他收费接口') from None
        self.done.clear()
        self.closing.clear()
        self.assembler = ResultAssembler()
        self.sent_samples = self.silent_samples = 0
        self.next_send = 0
        self.reader = threading.Thread(target=self._receive, name='xfyun-result', daemon=True)
        self.reader.start()
        LOGGER.info('讯飞流式会话已连接，今日本项目调用 %s/%s', used, self.config.xfyun_daily_limit)

    def _receive(self):
        try:
            while not self.closing.is_set():
                try:
                    data = self.ws.recv()
                except self.module.WebSocketTimeoutException:
                    continue
                if not data:
                    if not self.done.is_set():
                        raise RuntimeError('讯飞连接提前关闭')
                    return
                result = self.assembler.accept(json.loads(data))
                with self.lock:
                    if result['text']:
                        self.results.append(result)
                if result['final']:
                    self.done.set()
                    return
        except Exception as exc:
            if not self.closing.is_set():
                with self.lock:
                    # 不记录签名 URL、APIKey 或原始网络异常。
                    self.error = str(exc) if isinstance(exc, RuntimeError) else '讯飞接收异常，请检查网络和返回格式'
                self.done.set()

    def _send(self, pcm):
        first = self.sent_samples == 0
        msg = {'data': {'status': 0 if first else 1, 'format': 'audio/L16;rate=16000',
                       'encoding': 'raw', 'audio': base64.b64encode(pcm).decode()}}
        if first:
            msg.update(common={'app_id': self.keys['XFYUN_APP_ID']},
                       business={'language': 'zh_cn', 'domain': 'iat', 'accent': 'mandarin',
                                 'dwa': 'wpgs', 'rlang': 'zh-cn', 'eos': 2000})
        delay = self.next_send - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        try:
            self.ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception:
            raise RuntimeError('讯飞音频发送失败，请检查网络') from None
        self.next_send = time.monotonic() + len(pcm) / 32000
        self.sent_samples += len(pcm) // 2

    def _finish(self):
        try:
            if not self.done.is_set():
                try:
                    self.ws.send(json.dumps({'data': {'status': 2}}))
                except Exception:
                    raise RuntimeError('讯飞结束帧发送失败') from None
                if not self.done.wait(self.config.xfyun_final_timeout):
                    raise RuntimeError('讯飞句末结果返回超时，请检查网络')
        finally:
            self._drop_session()

    def _drop_session(self):
        self.closing.set()
        if self.ws is not None:
            self.ws.close(timeout=.2)
        if self.reader:
            self.reader.join(.5)
            if self.reader.is_alive():
                raise RuntimeError('讯飞接收线程未结束')
        self.ws = None
        self.reader = None
        self.pending.clear()
        self.trigger_samples = 0

    def process(self, pcm):
        with self.lock:
            if self.error:
                raise RuntimeError(self.error)
        if self.ws is not None and self.done.is_set():
            self._drop_session()
        converted, rms = self.encoder.convert(pcm)
        if not converted:
            return {'text': '', 'final': False}
        voiced = rms >= self.config.silence_rms
        if self.ws is None:
            self.pending.append(converted)
            self.trigger_samples = self.trigger_samples + len(converted)//2 if voiced else 0
            if self.trigger_samples >= self.config.xfyun_trigger_seconds * 16000:
                self._open()
                for chunk in self.pending:
                    self._send(chunk)
                self.pending.clear()
        else:
            self._send(converted)
            self.silent_samples = 0 if voiced else self.silent_samples + len(converted)//2
            if (self.silent_samples >= self.config.silence_seconds * 16000
                    or self.sent_samples >= self.config.xfyun_max_seconds * 16000):
                self._finish()
        with self.lock:
            if self.error:
                raise RuntimeError(self.error)
            result = self.results[-1] if self.results else {'text': '', 'final': False}
            self.results.clear()
        return result

    def close(self):
        # 停止按钮取消当前会话，停止后不回写尾部结果。
        self._drop_session()

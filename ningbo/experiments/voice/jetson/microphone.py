"""USB 麦克风的 PCM 输入，只负责设备选择、采音队列和连接关闭。"""
import math
import queue
import re
import threading
import logging

LOGGER = logging.getLogger(__name__)


class AudioBacklogError(OSError):
    """采音积压导致丢块，当前句需要重置，麦克风仍可继续使用。"""


def audio_inputs(module=None):
    if module is None:
        import sounddevice as module
    return [{"index": index, **dict(info)} for index, info in enumerate(module.query_devices())
            if info["max_input_channels"] > 0]


def audio_outputs(module=None):
    if module is None:
        import sounddevice as module
    return [{"index": index, **dict(info)} for index, info in enumerate(module.query_devices())
            if info.get('max_output_channels', 0) > 0]


def select_audio_device(selected, module, direction='input'):
    devices = audio_inputs(module) if direction == 'input' else audio_outputs(module)
    if isinstance(selected, int):
        matches = [info for info in devices if info['index'] == selected]
    else:
        selected = selected.strip()
        node = re.fullmatch(r'/dev/snd/pcmC(\d+)D(\d+)c', selected)
        if node is not None:
            selected = 'hw:%s,%s' % (int(node.group(1)), int(node.group(2)))
        elif selected.startswith('/dev/'):
            raise ValueError('USB 麦克风须使用 /dev/snd/pcmC<声卡号>D<设备号>c 采音节点，或音频编号/名称')
        if selected == 'auto':
            preferred = [info for info in devices if any(name in info['name'].casefold()
                         for name in ('aimic-m4', 'aispeech'))]
            matches = preferred or [info for info in devices if 'usb' in info['name'].casefold()]
        elif re.fullmatch(r'hw:(\d+),(\d+)', selected):
            pattern = re.compile(r'(?<!\w)' + re.escape(selected) + r'(?!\d)', re.IGNORECASE)
            matches = [info for info in devices if pattern.search(info['name'])]
        else:
            matches = [info for info in devices if selected.casefold() in info['name'].casefold()]
    if len(matches) != 1:
        choices = ', '.join('%s: %s' % (info['index'], info['name']) for info in devices) or '无设备'
        raise RuntimeError('USB 音频%s匹配到 %s 个设备，请按名称或硬件路径指定。可选: %s'
                           % ('输入' if direction == 'input' else '输出', len(matches), choices))
    return matches[0]


class UsbMicrophone:
    def __init__(self, config, module=None):
        self.config = config
        self.module = module
        self.device_index = None
        self.device_name = ""
        self.output_device_index = None
        self.output_device_name = ''
        self.sample_rate = None
        self._blocks = queue.Queue(maxsize=config.audio_queue_blocks)
        self._closed = threading.Event()
        self._lock = threading.RLock()
        self._stream = None
        self._error = ""
        self.input_overflows = 0
        self._backlog = threading.Event()

    def prepare(self):
        if self.module is None:
            import sounddevice
            self.module = sounddevice
        info = select_audio_device(self.config.input_device, self.module)
        self.device_index, self.device_name = info["index"], info["name"]
        if info.get('max_output_channels', 0) > 0:
            self.output_device_index, self.output_device_name = info['index'], info['name']
        LOGGER.info('USB 语音输入已选择：%s，当前编号 %s', self.device_name, self.device_index)
        rate = self.config.sample_rate if self.config.sample_rate is not None else info["default_samplerate"]
        if not math.isfinite(rate) or rate < 8000:
            raise ValueError("麦克风默认采样率无效")
        self.sample_rate = int(rate)
        self.module.check_input_settings(device=self.device_index, samplerate=self.sample_rate, channels=1, dtype="int16")
        return self.sample_rate

    def start(self):
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("麦克风采音已取消")
            if self._stream is not None:
                return
            if self.sample_rate is None:
                self.prepare()
            self._stream = self.module.RawInputStream(
                device=self.device_index, samplerate=self.sample_rate, channels=1, dtype="int16",
                blocksize=max(1, self.sample_rate * self.config.block_duration_ms // 1000), callback=self._callback,
                latency=self.config.input_latency_seconds,
            )
            try:
                self._stream.start()
            except Exception:
                self.close()
                raise

    def _callback(self, data, frames, time_info, status):
        if self._closed.is_set():
            return
        if status:
            if getattr(status, 'input_overflow', False):
                self.input_overflows += 1
                if self.input_overflows == 1:
                    LOGGER.warning("麦克风输入缓冲溢出，部分采样丢失；继续采音")
            else:
                self._error = f"麦克风采集异常: {status}"
                return
        try:
            self._blocks.put_nowait(bytes(data))
        except queue.Full:
            self._backlog.set()

    def read(self, timeout):
        if self._error:
            raise OSError(self._error)
        if self._backlog.is_set():
            self._backlog.clear()
            while True:
                try:
                    self._blocks.get_nowait()
                except queue.Empty:
                    break
            raise AudioBacklogError("麦克风音频积压已清理，请重新说话")
        if self._closed.is_set():
            return None
        with self._lock:
            if self._stream is not None and not self._stream.active:
                raise OSError("麦克风输入流已断开")
        try:
            return self._blocks.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self._closed.set()
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()

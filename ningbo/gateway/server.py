"""宁波实验箱 Web 网关（仅标准库）。

挂在 NingBoApplication 同进程内，复用 DeviceService，不单独打开硬件。
环境变量：
  NINGBO_GATEWAY_ENABLED=1
  NINGBO_GATEWAY_HOST=0.0.0.0
  NINGBO_GATEWAY_PORT=8080
  NINGBO_GATEWAY_TOKEN=   # 可选；设置后才要求 Bearer token，默认免登录
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

LOGGER = logging.getLogger("ningbo.gateway")
STATIC_DIR = Path(__file__).resolve().parent / "static"
ACTUATOR_NAMES = ("servo", "lock", "fan", "rgb", "stepper", "buzzer")


class GatewaySettings:
    def __init__(self):
        self.enabled = os.environ.get("NINGBO_GATEWAY_ENABLED", "1").strip().lower() not in (
            "0", "false", "no", "off")
        self.host = os.environ.get("NINGBO_GATEWAY_HOST", "0.0.0.0").strip() or "0.0.0.0"
        self.port = int(os.environ.get("NINGBO_GATEWAY_PORT", "8080") or "8080")
        token = os.environ.get("NINGBO_GATEWAY_TOKEN", "").strip()
        # 默认局域网免登录；仅当显式设置 NINGBO_GATEWAY_TOKEN 时才启用鉴权。
        self.token = token or ""
        self.require_auth = bool(token)


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _sensor_public(sensors: dict) -> dict:
    return dict(sensors or {})


def _read_adc_summary(devices) -> dict:
    """读取主/副 ADS1115 四路电压。副片地址来自 feedback_addresses。"""
    cfg = getattr(devices, "config", None)
    ads = getattr(cfg, "ads1115", None) if cfg else None
    monitor = getattr(devices.manager if hasattr(devices, "manager") else devices,
                      "adc_monitor", None)
    if monitor is None or ads is None or not getattr(ads, "enabled", False):
        return {"enabled": False, "chips": []}

    addresses = [ads.address]
    feedback = getattr(ads, "feedback_addresses", None) or {}
    for addr in feedback.values():
        if addr not in addresses:
            addresses.append(int(addr))

    chips = []
    for address in addresses:
        channels = []
        error = None
        for ch in range(4):
            try:
                channels.append(monitor.read_channel(ch, address=address))
            except Exception as exc:
                error = str(exc)
                break
        if error is None:
            chips.append({
                "address": hex(address),
                "bus": getattr(cfg, "i2c_bus", None),
                "full_scale_volts": ads.full_scale_volts,
                "ok": True,
                "channels": channels,
            })
        else:
            chips.append({
                "address": hex(address),
                "bus": getattr(cfg, "i2c_bus", None),
                "ok": False,
                "error": error,
                "channels": [],
            })
    return {
        "enabled": True,
        "chips": chips,
        "sample_time": __import__("time").time(),
        "feedback_channels": getattr(ads, "feedback_channels", {}) or {},
    }


def _actuator_payload(name: str, body: dict) -> dict:
    if name not in ACTUATOR_NAMES:
        raise ValueError(f"未知执行器: {name}")
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON 对象")

    if name in ("buzzer", "lock", "fan"):
        if "on" not in body:
            raise ValueError(f"{name} 需要布尔字段 on")
        return {"on": bool(body["on"])}

    if name == "servo":
        if body.get("sweep"):
            return {"sweep": True}
        if "angle" not in body:
            raise ValueError("servo 需要 angle 或 sweep")
        angle = float(body["angle"])
        if angle < 0 or angle > 180:
            raise ValueError("servo angle 必须在 0~180")
        return {"angle": angle}

    if name == "stepper":
        direction = str(body.get("dir", body.get("direction", "stop"))).lower()
        aliases = {"forward": "fwd", "backward": "rev", "back": "rev", "fwd": "fwd",
                   "rev": "rev", "stop": "stop"}
        direction = aliases.get(direction, direction)
        if direction not in ("fwd", "rev", "stop"):
            raise ValueError("stepper.dir 必须是 fwd/rev/stop")
        n = int(body.get("n", body.get("steps", 0)) or 0)
        spd = body.get("spd", body.get("speed_ms"))
        spd = int(spd) if spd is not None else None
        if direction != "stop" and n <= 0:
            raise ValueError("stepper 正反转需要 n>0")
        return {"dir_": direction, "n": max(0, n), "spd": spd}

    if name == "rgb":
        if body.get("off"):
            return {"off": True}
        if "color" in body and body["color"] not in (None, ""):
            return {"color": str(body["color"]).lower()}
        if any(key in body for key in ("r", "g", "b", "red", "green", "blue")):
            return {
                "r": bool(body.get("r", body.get("red", False))),
                "g": bool(body.get("g", body.get("green", False))),
                "b": bool(body.get("b", body.get("blue", False))),
            }
        raise ValueError("rgb 需要 color / r,g,b / off")

    raise ValueError(f"未处理的执行器: {name}")


class GatewayHandler(BaseHTTPRequestHandler):
    devices = None
    settings: GatewaySettings | None = None
    camera_preview = None
    camera_control = None
    camera_stream_slots = threading.BoundedSemaphore(4)
    protocol_version = "HTTP/1.1"
    server_version = "NingBoGateway/1.0"

    def log_message(self, fmt, *args):
        LOGGER.debug("gateway %s - %s", self.address_string(), fmt % args)

    def _settings(self) -> GatewaySettings:
        return self.settings or GatewaySettings()

    def _send(self, status: int, payload=None, raw: bytes | None = None,
              content_type: str = "application/json; charset=utf-8"):
        if raw is None:
            raw = _json_bytes(payload if payload is not None else {})
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _unauthorized(self):
        self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "需要访问令牌"})

    _adc_cache = {"at": 0.0, "data": None}
    _adc_lock = threading.Lock()

    def _adc_cached(self, ttl: float = 1.5):
        import time as _time
        with self._adc_lock:
            now = _time.time()
            if self._adc_cache["data"] is not None and now - self._adc_cache["at"] < ttl:
                return self._adc_cache["data"]
            try:
                data = _read_adc_summary(self.devices)
            except Exception as exc:
                data = {"enabled": True, "error": str(exc), "chips": []}
            self._adc_cache["at"] = now
            self._adc_cache["data"] = data
            return data

    def _check_auth(self) -> bool:
        settings = self._settings()
        if not settings.require_auth:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header[7:].strip()
        else:
            token = self.headers.get("X-Gateway-Token", "").strip()
        if token and hmac.compare_digest(token, settings.token):
            return True
        return False

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > 64 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        if not raw.strip():
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data

    def _stream_camera(self):
        preview = self.camera_preview
        if preview is None:
            self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                       {"ok": False, "error": "摄像头预览未启用"})
            return
        if not self.camera_stream_slots.acquire(blocking=False):
            self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                       {"ok": False, "error": "视频流连接数已满"})
            return
        try:
            hub = preview.hub
            if hub.snapshot()["state"] != "streaming":
                self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                           {"ok": False, "error": "摄像头未启动；请先申请摄像头租约"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sequence = 0
            while True:
                record = hub.wait_frame(sequence, timeout=1.0)
                if record is None:
                    if hub.snapshot()["state"] != "streaming":
                        break
                    continue
                sequence, jpeg = record
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(jpeg)).encode("ascii") + b"\r\n\r\n" + jpeg + b"\r\n"
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            self.camera_stream_slots.release()

    def do_OPTIONS(self):
        self._send(HTTPStatus.NO_CONTENT, raw=b"")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = STATIC_DIR / "index.html"
            if page.is_file():
                self._send(HTTPStatus.OK, raw=page.read_bytes(),
                           content_type="text/html; charset=utf-8")
                return
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "index.html 缺失"})
            return

        if path in ("/camera", "/camera.html"):
            page = STATIC_DIR / "camera.html"
            if page.is_file():
                self._send(HTTPStatus.OK, raw=page.read_bytes(),
                           content_type="text/html; charset=utf-8")
            else:
                self._send(HTTPStatus.NOT_FOUND,
                           {"ok": False, "error": "camera.html 缺失"})
            return

        if path == "/api/health":
            self._send(HTTPStatus.OK, {"ok": True, "service": "ningbo-gateway"})
            return

        if path.startswith("/api/"):
            if not self._check_auth():
                self._unauthorized()
                return
            if path == "/api/camera/status":
                if self.camera_preview is None:
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                               {"ok": False, "error": "摄像头预览未启用"})
                    return
                camera = self.camera_preview.hub.snapshot()
                if self.camera_control is not None:
                    try:
                        camera["leases"] = self.camera_control.camera_for_task().snapshot().get("subscribers", [])
                    except Exception:
                        camera["leases"] = []
                self._send(HTTPStatus.OK, {"ok": True, "camera": camera})
                return
            if path == "/api/camera/snapshot.jpg":
                if self.camera_preview is None:
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                               {"ok": False, "error": "摄像头预览未启用"})
                    return
                record = self.camera_preview.hub.wait_frame(after=0, timeout=0.2)
                if record is None:
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                               {"ok": False, "error": "当前没有可用画面；请先申请摄像头租约"})
                    return
                _, jpeg = record
                self._send(HTTPStatus.OK, raw=jpeg, content_type="image/jpeg")
                return
            if path == "/api/camera/stream.mjpg":
                self._stream_camera()
                return
            if path == "/api/sensors":
                snap = self.devices.snapshot()
                self._send(HTTPStatus.OK, {
                    "ok": True,
                    "time": snap["meta"]["time"],
                    "sensors": _sensor_public(snap.get("sensors")),
                    "adc": self._adc_cached(),
                })
                return
            if path == "/api/adc":
                self._send(HTTPStatus.OK, {"ok": True, "adc": self._adc_cached()})
                return
            if path == "/api/actuators":
                snap = self.devices.snapshot()
                self._send(HTTPStatus.OK, {
                    "ok": True,
                    "time": snap["meta"]["time"],
                    "actuators": snap.get("actuators"),
                })
                return
            if path == "/api/state":
                snap = self.devices.snapshot()
                self._send(HTTPStatus.OK, {
                    "ok": True,
                    "meta": {
                        "mode": snap["meta"]["mode"],
                        "version": snap["meta"]["version"],
                        "time": snap["meta"]["time"],
                        "init_errors": snap["meta"]["init_errors"],
                    },
                    "sensors": snap.get("sensors"),
                    "actuators": snap.get("actuators"),
                    "adc": self._adc_cached(),
                })
                return
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"未知接口 {path}"})
            return

        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        if not self._check_auth():
            self._unauthorized()
            return
        try:
            body = self._read_json()
        except Exception as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"JSON 解析失败: {exc}"})
            return

        try:
            if path == "/api/camera/lease":
                if self.camera_preview is None or self.camera_control is None:
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE,
                               {"ok": False, "error": "摄像头共享控制未启用"})
                    return
                import re
                lease_id = body.get("lease_id")
                active = body.get("active")
                if not isinstance(lease_id, str) or re.fullmatch(r"[a-f0-9]{32}", lease_id) is None:
                    raise ValueError("lease_id 必须是 32 位小写十六进制字符串")
                if not isinstance(active, bool):
                    raise ValueError("active 必须为布尔值")
                camera = self.camera_control.camera_for_task()
                owner = "gateway:" + lease_id
                if active:
                    camera.acquire(owner, ttl=15.0)
                else:
                    camera.release(owner)
                self._send(HTTPStatus.OK, {
                    "ok": True,
                    "active": active,
                    "camera": camera.snapshot(),
                })
                return

            if path == "/api/emergency_stop":
                result = self.devices.emergency_stop()
                self._send(HTTPStatus.OK, {"ok": bool(result.get("ok", True)), "result": result})
                return

            if path.startswith("/api/actuators/"):
                name = path.rsplit("/", 1)[-1]
                kwargs = _actuator_payload(name, body)
                result = self.devices.control(name, **kwargs)
                self._send(HTTPStatus.OK, {"ok": True, "name": name, "result": result})
                return

            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"未知接口 {path}"})
        except ValueError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            LOGGER.exception("网关控制失败: %s %s", path, body)
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def start_gateway(devices, settings: GatewaySettings | None = None,
                  camera_preview=None, camera_control=None) -> ThreadingHTTPServer | None:
    settings = settings or GatewaySettings()
    if not settings.enabled:
        LOGGER.info("网关已禁用")
        return None

    handler = type("BoundGatewayHandler", (GatewayHandler,), {
        "devices": devices,
        "settings": settings,
        "camera_preview": camera_preview,
        "camera_control": camera_control,
    })
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="ningbo-gateway", daemon=True)
    thread.start()
    token_hint = "免登录" if not settings.require_auth else f"token={settings.token}"
    LOGGER.info(
        "Web 网关已启动: http://%s:%s/  %s  传感器+执行器+摄像头控制",
        settings.host, settings.port, token_hint,
    )
    return server

"""Web 网关：同进程内 HTTP 访问现有 DeviceService。"""
from gateway.server import GatewaySettings, start_gateway

__all__ = ("GatewaySettings", "start_gateway")

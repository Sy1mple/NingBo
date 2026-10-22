# Jetson 摄像头与执行器 HTTP 接口

地址：`http://192.168.124.9:8080`。接口由 `/opt/ningbo` 的同进程 Web 网关提供，复用已运行的设备管理器和摄像头采集线程。

## 蜂鸣器

```http
POST /api/actuators/buzzer
Content-Type: application/json

{"on": true}
```

关闭：`{"on": false}`。应使用 JSON 布尔值。

## RGB 三色灯

```http
POST /api/actuators/rgb
Content-Type: application/json

{"color": "red"}
```

颜色支持 `red`、`green`、`blue`、`yellow`、`cyan`、`magenta`、`white`；关闭：`{"off": true}`。也可用 `{"r": true, "g": false, "b": false}`。

## 摄像头

状态：`GET /api/camera/status`

启动/续租共享采集：

```http
POST /api/camera/lease
Content-Type: application/json

{"lease_id": "0123456789abcdef0123456789abcdef", "active": true}
```

`lease_id` 是客户端生成的 32 位小写十六进制字符串。租约 15 秒过期；采集期间每 5 秒重复发送 `active: true` 续租。停止采集并释放租约：同一 `lease_id` 发送 `{"active": false}`。

单帧 JPEG：`GET /api/camera/snapshot.jpg`

MJPEG 视频流：`GET /api/camera/stream.mjpg`

摄像头未启动或没有新画面时，图片/视频接口返回 HTTP 503。支持的跨域请求由网关处理。

## 查看执行器状态

`GET /api/actuators` 返回蜂鸣器、RGB 灯和其他执行器状态；`GET /api/health` 检查网关是否运行。

当前网关未配置访问令牌，按现有设置可在实验室局域网调用；不要将端口 8080 转发到公网。接口只负责下发控制命令，不代表蜂鸣器或灯的物理动作已被反馈确认。

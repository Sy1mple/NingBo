"""One V4L2 MJPEG capture, NVIDIA decode for vision, JPEG passthrough for preview."""
from pathlib import Path
import logging

LOG=logging.getLogger(__name__)


class GstUsbCamera:
    backend='nvv4l2decoder-mjpeg'
    output_fps=30

    def __init__(self,config):
        self.config=config
        self.pipeline=None
        self.selected_device=None
        self.preview_jpeg=None

    def start(self):
        import gi
        gi.require_version('Gst','1.0')
        from gi.repository import Gst
        Gst.init(None)
        self.Gst=Gst
        from .camera import camera_nodes
        source=self.config.device
        if source=='auto':
            nodes=[node for node in camera_nodes() if node.get('usb') and node.get('capture')]
            if not nodes or len({node['bus'] for node in nodes})!=1:
                raise OSError('需要一台可采集的 USB 摄像头，或显式指定设备')
            source=nodes[0]['path']
        path=Path(str(source)).resolve()
        if path.parent!=Path('/dev') or not path.name.startswith('video') or not path.name[5:].isdigit():
            raise ValueError('硬件采集需要本地 /dev/videoN 设备')
        for element in ('v4l2src','jpegparse','nvv4l2decoder','nvvidconv','videoconvert','appsink'):
            if Gst.ElementFactory.find(element) is None:
                raise RuntimeError('缺少 GStreamer 插件: '+element)
        width,height=int(self.config.width),int(self.config.height)
        if width<=0 or height<=0:
            raise ValueError('摄像头尺寸无效')
        description=(
            'v4l2src device=%s do-timestamp=true ! image/jpeg,width=%d,height=%d,framerate=30/1 ! jpegparse ! tee name=encoded '
            'encoded. ! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream ! '
            'appsink name=jpeg sync=false max-buffers=1 drop=true '
            'encoded. ! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream ! '
            'nvv4l2decoder name=hardware_decoder mjpeg=true ! nvvidconv ! video/x-raw,format=BGRx ! '
            'videoconvert ! video/x-raw,format=BGR ! appsink name=decoded sync=false max-buffers=1 drop=true'
        ) % (path,width,height)
        self.pipeline=Gst.parse_launch(description)
        self.jpeg_sink=self.pipeline.get_by_name('jpeg')
        self.decoded_sink=self.pipeline.get_by_name('decoded')
        self.selected_device=str(path)
        if self.pipeline.set_state(Gst.State.PLAYING)==Gst.StateChangeReturn.FAILURE:
            self.close()
            raise RuntimeError('NVIDIA 硬件采集管线启动失败')
        LOG.info('USB camera hardware decode: %s, MJPEG %dx%d@30, JPEG passthrough',path,width,height)

    def read(self):
        import numpy as np
        Gst=self.Gst
        sample=self.decoded_sink.emit('try-pull-sample',2*Gst.SECOND)
        if sample is None:
            message=self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            raise RuntimeError(str(message.parse_error()) if message else '硬件解码未返回图像')
        caps=sample.get_caps().get_structure(0)
        width,height=caps.get_value('width'),caps.get_value('height')
        buffer=sample.get_buffer()
        ok,mapped=buffer.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError('无法读取硬件解码帧')
        try:
            stride=len(mapped.data)//height
            if stride<width*3:
                raise RuntimeError('硬件解码帧步幅无效')
            frame=np.ndarray((height,width,3),dtype=np.uint8,buffer=mapped.data,strides=(stride,3,1)).copy()
        finally:
            buffer.unmap(mapped)
        jpeg=self.jpeg_sink.emit('try-pull-sample',Gst.SECOND//10)
        if jpeg is not None:
            buffer=jpeg.get_buffer()
            self.preview_jpeg=buffer.extract_dup(0,buffer.get_size())
        return frame

    def close(self):
        pipeline,self.pipeline=self.pipeline,None
        if pipeline is not None:
            pipeline.set_state(self.Gst.State.NULL)
            pipeline.get_state(3*self.Gst.SECOND)
        self.preview_jpeg=None
        self.selected_device=None

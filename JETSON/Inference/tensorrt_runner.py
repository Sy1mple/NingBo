"""JetPack TensorRT 8.x 固定输入推理，CUDA Runtime 分配缓冲，无 CPU 推理后备。"""
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import platform
import uuid


class CudaRuntime:
    def __init__(self, device):
        name = ctypes.util.find_library("cudart") or "libcudart.so"
        try:
            self.lib = ctypes.CDLL(name)
        except OSError as exc:
            raise RuntimeError("未找到 CUDA Runtime；请在 Jetson 的 JetPack CUDA 环境运行") from exc
        self.lib.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        self.lib.cudaFree.argtypes = [ctypes.c_void_p]
        self.lib.cudaMemcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        self.lib.cudaSetDevice.argtypes = [ctypes.c_int]
        self.lib.cudaGetErrorString.argtypes = [ctypes.c_int]
        self.lib.cudaGetErrorString.restype = ctypes.c_char_p
        self.device = device
        self.select()
        self.check(self.lib.cudaFree(ctypes.c_void_p()))

    def check(self, result):
        if result:
            note = self.lib.cudaGetErrorString(result)
            raise RuntimeError(f"CUDA 操作失败 {result}: {note.decode() if note else 'unknown'}")

    def select(self):
        self.check(self.lib.cudaSetDevice(self.device))

    def malloc(self, size):
        pointer = ctypes.c_void_p()
        self.check(self.lib.cudaMalloc(ctypes.byref(pointer), size))
        return pointer.value

    def free(self, pointer):
        self.check(self.lib.cudaFree(ctypes.c_void_p(pointer)))

    def copy_to_device(self, pointer, array):
        self.check(self.lib.cudaMemcpy(ctypes.c_void_p(pointer), ctypes.c_void_p(array.ctypes.data), array.nbytes, 1))

    def copy_from_device(self, array, pointer):
        self.check(self.lib.cudaMemcpy(ctypes.c_void_p(array.ctypes.data), ctypes.c_void_p(pointer), array.nbytes, 2))

    def synchronize(self):
        self.check(self.lib.cudaDeviceSynchronize())

    def identity(self):
        version = ctypes.c_int()
        self.check(self.lib.cudaRuntimeGetVersion(ctypes.byref(version)))
        # 缓存跟随本机，不能把 Windows/其他板的 engine 当作可移植 ONNX。
        return {"cuda": version.value, "device": self.device, "machine": platform.machine(), "host": platform.node()}


class TensorRTRunner:
    def __init__(self, model_path, input_shape, config):
        import numpy as np
        try:
            import tensorrt as trt
        except ImportError as exc:
            raise RuntimeError("TensorRT Python 不可用；需使用 JetPack 自带的 TensorRT 8.x，不会转 CPU") from exc
        if int(trt.__version__.split('.')[0]) != 8:
            raise RuntimeError(f"Xavier NX 适配器需要 JetPack TensorRT 8.x，当前为 {trt.__version__}")
        self.np, self.trt, self.config = np, trt, config
        self.cuda = CudaRuntime(config.cuda_device)
        self.logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self.logger)
        if config.dla_core is not None:
            self.runtime.DLA_core = config.dla_core
        self._buffers = []
        self.context = None
        self.engine = None
        path = Path(model_path)
        identity = {**self.cuda.identity(), "trt": trt.__version__, "model": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "shape": list(input_shape), "precision": config.precision, "dla": config.dla_core,
                    "gpu_fallback": config.dla_gpu_fallback, "workspace_mb": config.workspace_mb}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cache = Path(config.engine_cache) / (key + ".engine")
        serialized = cache.read_bytes() if cache.exists() else self._build(path, input_shape)
        self.engine = self.runtime.deserialize_cuda_engine(serialized)
        if self.engine is None:
            raise RuntimeError(f"TensorRT 引擎加载失败：{cache}；不会转 CPU，请检查引擎缓存和 JetPack 版本")
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_name(key + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_bytes(serialized)
            temporary.replace(cache)
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("TensorRT 无法创建执行上下文")
        try:
            for index in range(self.engine.num_bindings):
                shape = tuple(self.context.get_binding_shape(index))
                if any(dim <= 0 for dim in shape):
                    raise RuntimeError("TensorRT 引擎仍有未确定输入/输出维度")
                types = {trt.DataType.FLOAT: np.float32, trt.DataType.HALF: np.float16,
                         trt.DataType.INT8: np.int8, trt.DataType.INT32: np.int32, trt.DataType.BOOL: np.bool_}
                dtype = self.engine.get_binding_dtype(index)
                if dtype not in types:
                    raise RuntimeError("TensorRT 返回了未支持的缓冲数据类型")
                array = np.empty(shape, dtype=types[dtype])
                self._buffers.append({"array": array, "pointer": self.cuda.malloc(array.nbytes),
                                      "name": self.engine.get_binding_name(index), "input": self.engine.binding_is_input(index)})
            inputs = [buffer for buffer in self._buffers if buffer['input']]
            if len(inputs) != 1 or tuple(inputs[0]['array'].shape) != tuple(input_shape):
                raise RuntimeError("TensorRT 当前仅支持已配置的一路固定图像输入")
        except Exception:
            self.close()
            raise

    def _build(self, path, shape):
        trt = self.trt
        builder = trt.Builder(self.logger)
        network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
        parser = trt.OnnxParser(network, self.logger)
        if not parser.parse(path.read_bytes()):
            errors = '; '.join(str(parser.get_error(i)) for i in range(parser.num_errors))
            raise RuntimeError(f"TensorRT ONNX 解析失败：{path.name}: {errors}")
        if network.num_inputs != 1:
            raise RuntimeError("人脸模型必须只有一路输入")
        native_shape = tuple(network.get_input(0).shape)
        if all(dim > 0 for dim in native_shape) and native_shape != tuple(shape):
            raise ValueError(f"ONNX 固定输入为 {native_shape}，请求为 {tuple(shape)}；不能直接更改带固定 reshape 的模型尺寸")
        network.get_input(0).shape = tuple(shape)
        settings = builder.create_builder_config()
        workspace = self.config.workspace_mb * 1024 * 1024
        if hasattr(settings, "set_memory_pool_limit"):
            settings.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace)
        else:
            settings.max_workspace_size = workspace
        if self.config.precision == "fp16":
            if not builder.platform_has_fast_fp16:
                raise RuntimeError("当前 GPU 不支持快速 FP16，拒绝隐藏更换精度")
            settings.set_flag(trt.BuilderFlag.FP16)
        if self.config.dla_core is not None:
            if self.config.dla_core >= builder.num_DLA_cores:
                raise RuntimeError("当前设备没有所选 DLA 核")
            settings.default_device_type = trt.DeviceType.DLA
            settings.DLA_core = self.config.dla_core
            if self.config.dla_gpu_fallback:
                settings.set_flag(trt.BuilderFlag.GPU_FALLBACK)
        serialized = builder.build_serialized_network(network, settings)
        if serialized is None:
            raise RuntimeError("TensorRT 构建失败，请查看解析/算子日志；DLA 不兼容时可使用 GPU，不会转 CPU")
        return bytes(serialized)

    def infer(self, image):
        self.cuda.select()
        source = next(buffer for buffer in self._buffers if buffer['input'])
        if tuple(image.shape) != tuple(source['array'].shape):
            raise ValueError("推理输入尺寸与 TensorRT 引擎不符")
        self.np.copyto(source['array'], image, casting="same_kind")
        self.cuda.copy_to_device(source['pointer'], source['array'])
        if not self.context.execute_v2([buffer['pointer'] for buffer in self._buffers]):
            raise RuntimeError("TensorRT GPU/DLA 推理失败")
        self.cuda.synchronize()
        outputs = {}
        for buffer in self._buffers:
            if not buffer['input']:
                self.cuda.copy_from_device(buffer['array'], buffer['pointer'])
                outputs[buffer['name']] = buffer['array'].copy()
        return outputs

    def close(self):
        if self._buffers:
            self.cuda.select()
            self.cuda.synchronize()
            buffers, self._buffers = self._buffers, []
            for buffer in buffers:
                self.cuda.free(buffer['pointer'])
        self.context = None
        self.engine = None

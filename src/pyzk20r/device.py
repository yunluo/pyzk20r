# -*- coding: utf-8 -*-
"""设备层: Scanner (采集器) 与 Frame (单次采集帧)。

用法::

    from pyzk20r import Scanner

    with Scanner() as dev:
        tpl = dev.enroll()                    # 连续采集 3 次
        for frame in dev.capture():           # 持续采集生成器
            img = frame.to_bmp()
            break

子类可覆写 ``on_enroll_progress`` / ``acquire`` / ``try_acquire`` 等钩子。
语法兼容 Python 3.4+ (Windows XP): 类型标注用内联标注 (PEP 3107) + typing 构造
(3.4 无内置 typing, 声明了 ``typing; python_version < "3.5"`` 依赖自动补齐)。
"""

from __future__ import absolute_import

import ctypes
import struct
import time
from collections import namedtuple

from . import _binding
from ._binding import MAX_TEMPLATE_SIZE, Param, ZKFPErr
from .exceptions import (
    CaptureTimeoutError,
    DeviceError,
    DeviceNotFoundError,
    LibraryError,
    ZKFPMError,
    error_class,
    raise_for,
)

try:
    from typing import Generator, List, Optional, Tuple
except ImportError:  # pragma: no cover -- 精简环境缺 typing 时的提示
    raise ImportError("typing module not found, please install: pip install typing")

__all__ = ["DeviceInfo", "Frame", "Scanner"]

#: 允许静默重试的瞬态采集错误码 (通常表示"暂无手指")。
#: 不含 ABSORT/CANCEL —— 它们是显式中止信号, 持续出现说明有真实问题, 不应死循环。
_TRANSIENT_CAPTURE_CODES = frozenset(
    (
        ZKFPErr.CAPTURE,
        ZKFPErr.BUSY,
        ZKFPErr.TIMEOUT,
    )
)

#: LED 颜色名 -> 参数代码
_LED_CODES = {
    "white": Param.LED_WHITE,
    "green": Param.LED_GREEN,
    "red": Param.LED_RED,
}

#: 字符串参数读取缓冲区大小
_PARAM_STR_BUF = 256


class DeviceInfo(namedtuple("DeviceInfo", "vendor product serial vid_pid")):
    """设备静态信息。``vid_pid`` 形如 ``VID_1B55 PID_0223``。"""

    __slots__ = ()


class Frame:
    """一次成功采集: 原始灰度图像 + 提取出的指纹模板。"""

    __slots__ = ("image", "template", "width", "height", "dpi")

    def __init__(self, image: bytes, template: bytes, width: int, height: int, dpi: int) -> None:
        self.image = image
        self.template = template
        self.width = width
        self.height = height
        self.dpi = dpi

    def to_bmp(self) -> bytes:
        """图像转 8 位灰度 BMP 字节流 (纯 stdlib, 无 PIL 依赖)。"""
        if len(self.image) < self.width * self.height:
            raise ValueError(
                "image data too short: {:d} bytes < {:d}x{:d}".format(len(self.image), self.width, self.height)
            )
        row_size = self.width + (4 - self.width % 4) % 4
        body_size = row_size * self.height
        palette = b"".join(struct.pack("BBBB", i, i, i, 0) for i in range(256))
        offset = 14 + 40 + len(palette)
        header = struct.pack("<2sIHHI", b"BM", offset + body_size, 0, 0, offset)
        info = struct.pack("<IiiHHIIiiII", 40, self.width, self.height, 1, 8, 0, body_size, 0, 0, 0, 0)
        parts = [header, info, palette]
        for y in range(self.height - 1, -1, -1):  # BMP 自底向上
            start = y * self.width
            parts.append(self.image[start : start + self.width])
            parts.append(b"\x00" * (row_size - self.width))
        return b"".join(parts)

    def __repr__(self) -> str:
        return "Frame(template={:d} bytes, image={:d}x{:d}@{:d}dpi)".format(
            len(self.template), self.width, self.height, self.dpi
        )


class Scanner:
    """Live20R 指纹采集器。

    构造即打开设备 (无设备抛 :class:`DeviceNotFoundError`);
    支持 with 上下文管理, 退出自动关闭。

    线程模型: 本库是同步单线程设计, 不含内部线程; SDK 句柄与全局
    初始化计数均不加锁, 请勿在多个线程并发构造/关闭设备或并发采集。
    """

    def __init__(self, index: int = 0, dll_path: Optional[str] = None) -> None:
        self._index = int(index)
        self._dll_path = dll_path
        self._handle = None  # type: Optional[int]
        self.open()

    # -- 生命周期 -----------------------------------------------------------

    def open(self) -> None:
        """打开设备 (先确保 SDK 初始化)。"""
        if self._handle is not None:
            raise ZKFPMError(ZKFPErr.ALREADY_OPENED, "device already open")
        dll = _binding.lib_addref(self._dll_path)
        count = dll.ZKFPM_GetDeviceCount()
        if count < 0:
            # SDK 文档: 负值表示接口调用失败, 与"无设备连接"是两回事
            _binding.lib_release()
            raise LibraryError(int(count), "failed to query device count")
        if count < 1:
            _binding.lib_release()
            raise DeviceNotFoundError(ZKFPErr.NO_DEVICE, "no device connected")
        handle = dll.ZKFPM_OpenDevice(self._index)
        if not handle:
            _binding.lib_release()
            raise DeviceNotFoundError(ZKFPErr.OPEN, "failed to open device (index={:d})".format(self._index))
        self._handle = handle

    def close(self) -> None:
        """关闭设备并释放 SDK 引用。可安全重复调用。"""
        if self._handle is None:
            return
        ret = _binding.get_dll(self._dll_path).ZKFPM_CloseDevice(self._handle)
        self._handle = None
        _binding.lib_release()
        raise_for(ret, "failed to close device")

    @property
    def closed(self) -> bool:
        return self._handle is None

    def __enter__(self):
        return self

    def __exit__(self, exc_type: type, exc_val: BaseException, exc_tb: object) -> None:
        self.close()

    def _require_open(self) -> int:
        if self._handle is None:
            raise ZKFPMError(ZKFPErr.NOT_OPENED, "device not open")
        return self._handle

    def __repr__(self) -> str:
        return "Scanner(index={:d}, closed={})".format(self._index, self.closed)

    # -- 参数 ---------------------------------------------------------------

    def get_param_bytes(self, code: int, size: int) -> bytes:
        """读取参数原始字节。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_uint(size)
        ret = _binding.get_dll().ZKFPM_GetParameters(handle, int(code), buf, ctypes.byref(n))
        raise_for(ret, "failed to get parameter {:d}".format(code), DeviceError)
        return buf.raw[: n.value]

    def get_param_int(self, code: int) -> int:
        """读取 int 型参数。"""
        return struct.unpack("<i", self.get_param_bytes(code, 4))[0]

    def set_param_int(self, code: int, value: int) -> None:
        """写入 int 型参数。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(struct.pack("<i", int(value)), 4)
        ret = _binding.get_dll().ZKFPM_SetParameters(handle, int(code), buf, 4)
        raise_for(ret, "failed to set parameter {:d}".format(code), DeviceError)

    def get_param_str(self, code: int) -> str:
        """读取字符串型参数 (厂商/产品名/序列号)。忽略不可解码字节 (Windows ANSI)。"""
        raw = self.get_param_bytes(code, _PARAM_STR_BUF).split(b"\x00")[0]
        return raw.decode("mbcs", "ignore")

    @property
    def width(self) -> int:
        return self.get_param_int(Param.IMG_WIDTH)

    @property
    def height(self) -> int:
        return self.get_param_int(Param.IMG_HEIGHT)

    @property
    def dpi(self) -> int:
        return self.get_param_int(Param.DPI)

    @dpi.setter
    def dpi(self, value: int) -> None:
        self.set_param_int(Param.DPI, value)  # 儿童指纹建议 750/1000 (文档标注 LIVEID20R 支持写)

    @property
    def fake_detect(self) -> bool:
        return bool(self.get_param_int(Param.FAKE_SWITCH))

    @fake_detect.setter
    def fake_detect(self, enabled: bool) -> None:
        self.set_param_int(Param.FAKE_SWITCH, 1 if enabled else 0)

    @property
    def finger_is_live(self) -> bool:
        """最近一次采集是否为真手指 (需先开启 fake_detect)。低五位全 1 为真。"""
        return (self.get_param_int(Param.FAKE_STATUS) & 31) == 31

    @property
    def device_ok(self) -> bool:
        return self.get_param_int(Param.DEVICE_STATUS) == 1

    @property
    def template_format(self) -> str:
        """模板标准: 'ansi' (默认) / 'iso'; 写入仅 ISO/ANSI 版设备支持。"""
        return "iso" if self.get_param_int(Param.TEMPLATE_FORMAT) else "ansi"

    @template_format.setter
    def template_format(self, value: str) -> None:
        fmt = {"ansi": 0, "iso": 1}.get(value)
        if fmt is None:
            raise ValueError("template_format only supports 'ansi' / 'iso'")
        self.set_param_int(Param.TEMPLATE_FORMAT, fmt)

    def led(self, color: str, on: bool = True) -> None:
        """控制指示灯闪烁, ``color``: 'white' / 'green' / 'red'。

        灯效支持依赖硬件: 参数码 101/102/103 (只写) 在无灯效的机型上
        写入会被接受但读回 -5 (无效参数), 即命令成功但没有实际灯光;
        白灯由固件在采集时自动点亮, 不受本参数控制。
        """
        try:
            code = _LED_CODES[color]
        except KeyError:
            raise ValueError("color only supports 'white' / 'green' / 'red'")
        self.set_param_int(code, 1 if on else 0)

    def info(self) -> DeviceInfo:
        raw = self.get_param_bytes(Param.VID_PID, 4)
        if len(raw) < 4:  # 部分固件不回填, 防御 struct.error
            raw = (raw + b"\x00\x00\x00\x00")[:4]
        vid, pid = struct.unpack("<HH", raw)
        return DeviceInfo(
            vendor=self.get_param_str(Param.VENDOR),
            product=self.get_param_str(Param.PRODUCT_NAME),
            serial=self.get_param_str(Param.SERIAL_NUMBER),
            vid_pid="VID_{:04X} PID_{:04X}".format(vid, pid),
        )

    def template_quality(self, template: bytes) -> int:
        """评估模板质量 (0~100, 越高越好)。返回值为质量分, 负数才是错误码。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(bytes(template), len(template))
        ret = _binding.get_dll().ZKFPM_GetTemplateQuality(handle, buf, len(template))
        if ret < 0:
            raise_for(ret, "failed to get template quality", DeviceError)
        return ret

    # -- 采集 ---------------------------------------------------------------

    def capture_params(self) -> Tuple[int, int, int]:
        """返回 (width, height, dpi)。"""
        handle = self._require_open()
        w, h, dpi = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        ret = _binding.get_dll().ZKFPM_GetCaptureParamsEx(handle, ctypes.byref(w), ctypes.byref(h), ctypes.byref(dpi))
        raise_for(ret, "failed to get capture params", DeviceError)
        return w.value, h.value, dpi.value

    def acquire_image(self) -> bytes:
        """只取图像 (阻塞, 需有手指)。"""
        handle = self._require_open()
        width, height, _dpi = self.capture_params()
        buf = ctypes.create_string_buffer(width * height)
        ret = _binding.get_dll().ZKFPM_AcquireFingerprintImage(handle, buf, width * height)
        raise_for(ret, "failed to acquire fingerprint image")
        return buf.raw

    def try_acquire(self, params: Optional[Tuple[int, int, int]] = None) -> Optional[Frame]:
        """尝试采集一次; 瞬态失败 (暂无手指等) 返回 None, 其他错误抛异常。

        公开扩展点: ``params`` 为 (width, height, dpi), 采集循环内传入可避免
        每轮重复查询; ``None`` 表示内部自行获取。子类覆写时请保留该可选参数。
        """
        handle = self._require_open()
        if params is None:
            params = self.capture_params()
        width, height, dpi = params
        img_buf = ctypes.create_string_buffer(width * height)
        tpl_buf = ctypes.create_string_buffer(MAX_TEMPLATE_SIZE)
        tpl_len = ctypes.c_uint(MAX_TEMPLATE_SIZE)
        ret = _binding.get_dll().ZKFPM_AcquireFingerprint(
            handle, img_buf, width * height, tpl_buf, ctypes.byref(tpl_len)
        )
        if ret == ZKFPErr.OK:
            return Frame(img_buf.raw, tpl_buf.raw[: tpl_len.value], width, height, dpi)
        if int(ret) in _TRANSIENT_CAPTURE_CODES:
            return None
        raise error_class(int(ret))(int(ret), "capture failed")

    def acquire(self, poll_interval: float = 0.1, timeout: Optional[float] = None) -> Frame:
        """阻塞等待手指并采集一次。

        反复轮询直到取像成功 (语义与文档一致: 需有手指才返回)。
        ``timeout`` 为最长等待秒数, 超时抛 :class:`CaptureTimeoutError`
        (:class:`CaptureError` 子类, 与取像故障可区分);
        ``None`` 表示一直等到有手指 (Ctrl+C 可中断)。
        采集参数 (宽高/DPI) 在进入循环时读取一次并复用; 运行中改动
        参数请退出后重新调用。
        """
        params = self.capture_params()
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            frame = self.try_acquire(params)
            if frame is not None:
                return frame
            if deadline is not None and time.monotonic() >= deadline:
                raise CaptureTimeoutError(int(ZKFPErr.CAPTURE), "capture timeout ({}s)".format(timeout))
            if poll_interval > 0:
                time.sleep(poll_interval)

    def capture(self, poll_interval: float = 0.1) -> Generator[Frame, None, None]:
        """持续采集生成器: 反复尝试, 成功则 yield Frame, 失败休眠重试。

        ``poll_interval`` 为失败重试间隔秒数 (0 为连续轮询)。
        采集参数在生成器首次迭代时读取一次并复用。
        break 退出即可, 设备仍需 :meth:`close` 或 with 块结束才关闭。
        """
        params = self.capture_params()
        while True:
            frame = self.try_acquire(params)
            if frame is not None:
                yield frame
            elif poll_interval > 0:
                time.sleep(poll_interval)

    def enroll(self, poll_interval: float = 0.1, timeout: Optional[float] = None) -> List[bytes]:
        """连续采集 3 次 (SDK 登记合并 ZKFPM_DBMerge 固定要求 3 枚), 返回模板列表。

        每成功一次调用一次 :meth:`on_enroll_progress` 钩子。
        ``timeout`` 为整个登记流程的最长等待秒数 (含所有失败重试),
        超时抛 :class:`CaptureTimeoutError` (:class:`CaptureError` 子类);
        ``None`` 表示一直等。
        自助终端等无人值守场景务必传 timeout, 防止用户中途离开导致无限阻塞。
        """
        total = 3
        params = self.capture_params()
        deadline = None if timeout is None else time.monotonic() + timeout
        templates = []  # type: List[bytes]
        while len(templates) < total:
            frame = self.try_acquire(params)
            if frame is not None:
                templates.append(frame.template)
                self.on_enroll_progress(len(templates), total)
                continue
            if deadline is not None and time.monotonic() >= deadline:
                raise CaptureTimeoutError(int(ZKFPErr.CAPTURE), "enroll timeout ({}s)".format(timeout))
            if poll_interval > 0:
                time.sleep(poll_interval)
        return templates

    def on_enroll_progress(self, done: int, total: int) -> None:
        """登记进度钩子, 子类覆写。``done`` 从 1 计数。"""

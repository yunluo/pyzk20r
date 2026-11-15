# -*- coding: utf-8 -*-
"""设备层: Scanner (采集器) 与 Frame (单次采集帧)。

用法::

    from pyzk20r import Scanner

    with Scanner() as dev:
        tpl = dev.enroll()                    # 连续采集 3 次
        for frame in dev.capture():           # 持续采集生成器
            img = frame.to_bmp()
            break

子类可覆写 ``on_enroll_progress`` / ``acquire`` / ``_try_acquire`` 等钩子。
语法兼容 Python 3.4 (Windows XP): 参数/返回类型用内联标注 (PEP 3107),
typing 构造以字符串形式标注 (运行时不求值), 变量类型用注释。
"""
from __future__ import absolute_import

import collections.abc
import ctypes
import struct
import time
from collections import namedtuple

from . import _binding
from ._binding import MAX_TEMPLATE_SIZE, Param, ZKFPErr
from .exceptions import (
    CaptureError,
    DeviceError,
    DeviceNotFoundError,
    ZKFPMError,
    error_class,
    raise_for,
)

__all__ = ["DeviceInfo", "Frame", "Scanner"]

#: 允许静默重试的瞬态采集错误码 (通常表示"暂无手指")。
#: 不含 ABSORT/CANCEL —— 它们是显式中止信号, 持续出现说明有真实问题, 不应死循环。
_TRANSIENT_CAPTURE_CODES = frozenset((
    ZKFPErr.CAPTURE, ZKFPErr.BUSY, ZKFPErr.TIMEOUT,
))

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
                "image 数据不足: %d 字节 < %dx%d" % (len(self.image), self.width, self.height))
        row_size = self.width + (4 - self.width % 4) % 4
        body_size = row_size * self.height
        palette = b"".join(struct.pack("BBBB", i, i, i, 0) for i in range(256))
        offset = 14 + 40 + len(palette)
        header = struct.pack("<2sIHHI", b"BM", offset + body_size, 0, 0, offset)
        info = struct.pack(
            "<IiiHHIIiiII", 40, self.width, self.height, 1, 8, 0, body_size, 0, 0, 0, 0)
        parts = [header, info, palette]
        for y in range(self.height - 1, -1, -1):          # BMP 自底向上
            start = y * self.width
            parts.append(self.image[start:start + self.width])
            parts.append(b"\x00" * (row_size - self.width))
        return b"".join(parts)

    def __repr__(self) -> str:
        return "Frame(template=%d bytes, image=%dx%d@%ddpi)" % (
            len(self.template), self.width, self.height, self.dpi)


class Scanner:
    """Live20R 指纹采集器。

    构造即打开设备 (无设备抛 :class:`DeviceNotFoundError`);
    支持 with 上下文管理, 退出自动关闭。

    线程模型: 本库是同步单线程设计, 不含内部线程; SDK 句柄与全局
    初始化计数均不加锁, 请勿在多个线程并发构造/关闭设备或并发采集。
    """

    def __init__(self, index: int = 0, dll_path: "str | None" = None) -> None:
        self._index = int(index)
        self._dll_path = dll_path
        self._handle = None                            # type: int | None
        self.open()

    # -- 生命周期 -----------------------------------------------------------

    def open(self) -> None:
        """打开设备 (先确保 SDK 初始化)。"""
        if self._handle is not None:
            raise ZKFPMError(ZKFPErr.ALREADY_OPENED, "设备已打开")
        dll = _binding.lib_addref(self._dll_path)
        if dll.ZKFPM_GetDeviceCount() < 1:
            _binding.lib_release()
            raise DeviceNotFoundError(ZKFPErr.NO_DEVICE, "无设备连接")
        handle = dll.ZKFPM_OpenDevice(self._index)
        if not handle:
            _binding.lib_release()
            raise DeviceNotFoundError(ZKFPErr.OPEN, "打开设备失败 (index=%d)" % self._index)
        self._handle = handle

    def close(self) -> None:
        """关闭设备并释放 SDK 引用。可安全重复调用。"""
        if self._handle is None:
            return
        ret = _binding.get_dll(self._dll_path).ZKFPM_CloseDevice(self._handle)
        self._handle = None
        _binding.lib_release()
        raise_for(ret, "关闭设备失败")

    @property
    def closed(self) -> bool:
        return self._handle is None

    def __enter__(self) -> "Scanner":
        return self

    def __exit__(self, exc_type: type, exc_val: BaseException, exc_tb: object) -> None:
        self.close()

    def _require_open(self) -> int:
        if self._handle is None:
            raise ZKFPMError(ZKFPErr.NOT_OPENED, "设备未打开")
        return self._handle

    def __repr__(self) -> str:
        return "Scanner(index=%d, closed=%s)" % (self._index, self.closed)

    # -- 参数 ---------------------------------------------------------------

    def get_param_bytes(self, code: int, size: int) -> bytes:
        """读取参数原始字节。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_uint(size)
        ret = _binding.get_dll().ZKFPM_GetParameters(handle, int(code), buf, ctypes.byref(n))
        raise_for(ret, "读取参数 %d 失败" % code, DeviceError)
        return buf.raw[:n.value]

    def get_param_int(self, code: int) -> int:
        """读取 int 型参数。"""
        return struct.unpack("<i", self.get_param_bytes(code, 4))[0]

    def set_param_int(self, code: int, value: int) -> None:
        """写入 int 型参数。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(struct.pack("<i", int(value)), 4)
        ret = _binding.get_dll().ZKFPM_SetParameters(handle, int(code), buf, 4)
        raise_for(ret, "设置参数 %d 失败" % code, DeviceError)

    def get_param_str(self, code: int) -> str:
        """读取字符串型参数 (厂商/产品名/序列号)。忽略不可解码字节 (兼容 GBK 控制台)。"""
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
        self.set_param_int(Param.DPI, value)          # Live20R 可写, 例如儿童指纹建议 750/1000

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
        return "iso" if self.get_param_int(Param.TEMPLATE_FORMAT) else "ansi"

    @template_format.setter
    def template_format(self, value: str) -> None:
        fmt = {"ansi": 0, "iso": 1}.get(value)
        if fmt is None:
            raise ValueError("template_format 只支持 'ansi' / 'iso'")
        self.set_param_int(Param.TEMPLATE_FORMAT, fmt)

    def led(self, color: str, on: bool = True) -> None:
        """控制指示灯闪烁, ``color``: 'white' / 'green' / 'red'。"""
        try:
            code = _LED_CODES[color]
        except KeyError:
            raise ValueError("color 只支持 'white' / 'green' / 'red'")
        self.set_param_int(code, 1 if on else 0)

    def info(self) -> DeviceInfo:
        raw = self.get_param_bytes(Param.VID_PID, 4)
        if len(raw) < 4:                             # 部分固件不回填, 防御 struct.error
            raw = (raw + b"\x00\x00\x00\x00")[:4]
        vid, pid = struct.unpack("<HH", raw)
        return DeviceInfo(
            vendor=self.get_param_str(Param.VENDOR),
            product=self.get_param_str(Param.PRODUCT_NAME),
            serial=self.get_param_str(Param.SERIAL_NUMBER),
            vid_pid="VID_%04X PID_%04X" % (vid, pid),
        )

    def template_quality(self, template: bytes) -> int:
        """评估模板质量 (0~100, 越高越好)。返回值为质量分, 负数才是错误码。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(bytes(template), len(template))
        ret = _binding.get_dll().ZKFPM_GetTemplateQuality(handle, buf, len(template))
        if ret < 0:
            raise_for(ret, "获取模板质量失败", DeviceError)
        return ret

    # -- 采集 ---------------------------------------------------------------

    def capture_params(self) -> "tuple[int, int, int]":
        """返回 (width, height, dpi)。"""
        handle = self._require_open()
        w, h, dpi = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        ret = _binding.get_dll().ZKFPM_GetCaptureParamsEx(
            handle, ctypes.byref(w), ctypes.byref(h), ctypes.byref(dpi))
        raise_for(ret, "获取采集参数失败", DeviceError)
        return w.value, h.value, dpi.value

    def acquire_image(self) -> bytes:
        """只取图像 (阻塞, 需有手指)。"""
        handle = self._require_open()
        width, height, _dpi = self.capture_params()
        buf = ctypes.create_string_buffer(width * height)
        ret = _binding.get_dll().ZKFPM_AcquireFingerprintImage(handle, buf, width * height)
        raise_for(ret, "取像失败")
        return buf.raw

    def _try_acquire(self) -> "Frame | None":
        """尝试采集一次; 瞬态失败 (暂无手指等) 返回 None, 其他错误抛异常。"""
        handle = self._require_open()
        width, height, dpi = self.capture_params()
        img_buf = ctypes.create_string_buffer(width * height)
        tpl_buf = ctypes.create_string_buffer(MAX_TEMPLATE_SIZE)
        tpl_len = ctypes.c_uint(MAX_TEMPLATE_SIZE)
        ret = _binding.get_dll().ZKFPM_AcquireFingerprint(
            handle, img_buf, width * height, tpl_buf, ctypes.byref(tpl_len))
        if ret == ZKFPErr.OK:
            return Frame(img_buf.raw, tpl_buf.raw[:tpl_len.value], width, height, dpi)
        if int(ret) in _TRANSIENT_CAPTURE_CODES:
            return None
        raise error_class(int(ret))(int(ret), "采集失败")

    def acquire(self) -> Frame:
        """阻塞采集一次 (需有手指), 失败抛 :class:`CaptureError`。"""
        frame = self._try_acquire()
        if frame is None:
            raise CaptureError(int(ZKFPErr.CAPTURE), "取像失败")
        return frame

    def capture(self, poll_interval: float = 0.1) -> "collections.abc.Iterator[Frame]":
        """持续采集生成器: 反复尝试, 成功则 yield Frame, 失败休眠重试。

        ``poll_interval`` 为失败重试间隔秒数 (0 为连续轮询)。
        break 退出即可, 设备仍需 :meth:`close` 或 with 块结束才关闭。
        """
        while True:
            frame = self._try_acquire()
            if frame is not None:
                yield frame
            elif poll_interval > 0:
                time.sleep(poll_interval)

    def enroll(self, poll_interval: float = 0.1) -> "list[bytes]":
        """连续采集 3 次 (SDK 登记合并 ZKFPM_DBMerge 固定要求 3 枚), 返回模板列表。

        每成功一次调用一次 :meth:`on_enroll_progress` 钩子。
        """
        total = 3
        templates = []                                 # type: list[bytes]
        while len(templates) < total:
            frame = self._try_acquire()
            if frame is None:
                if poll_interval > 0:
                    time.sleep(poll_interval)
                continue
            templates.append(frame.template)
            self.on_enroll_progress(len(templates), total)
        return templates

    def on_enroll_progress(self, done: int, total: int) -> None:
        """登记进度钩子, 子类覆写。``done`` 从 1 计数。"""

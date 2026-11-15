# -*- coding: utf-8 -*-
"""pyzk20r — 中控智慧 (ZKTeco) Live20R 指纹采集器 Python 库。

基于官方 ZKFinger Reader SDK C API (libzkfp.dll) 的纯 ctypes 封装,
零第三方依赖, 支持 Windows XP / Python 3.7+。

快速上手::

    from pyzk20r import Scanner, FingerprintDB

    db = FingerprintDB()
    with Scanner() as dev:
        db.enroll(dev, user_id="A001")               # 登记一枚指纹 (采 3 次, 工号为字符串)
        uid, score = db.identify(dev.acquire().template)
"""

from . import _binding
from ._binding import MAX_TEMPLATE_SIZE, DBParam, Param, ZKFPErr
from .db import FingerprintDB
from .device import DeviceInfo, Frame, Scanner
from .exceptions import (
    CaptureError,
    DeviceError,
    DeviceNotFoundError,
    ExtractError,
    LibraryError,
    MatchError,
    MergeError,
    ZKFPMError,
)

__version__ = "0.1.2"

__all__ = [
    # 核心
    "Scanner",
    "Frame",
    "DeviceInfo",
    "FingerprintDB",
    # 枚举
    "ZKFPErr",
    "Param",
    "DBParam",
    "MAX_TEMPLATE_SIZE",
    # 异常
    "ZKFPMError",
    "DeviceError",
    "DeviceNotFoundError",
    "CaptureError",
    "MatchError",
    "MergeError",
    "ExtractError",
    "LibraryError",
    # 底层 (高级用法/测试注入)
    "_binding",
]

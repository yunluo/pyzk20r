# -*- coding: utf-8 -*-
"""异常层级: 单一基类携带 SDK 原始错误码, 常见语义错误为子类。

本模块不依赖 _binding, 避免循环导入; 错误码表使用原生 int。
"""
from __future__ import absolute_import

try:
    from typing import Optional
except ImportError:  # pragma: no cover -- 精简环境缺 typing 时的提示
    raise ImportError("缺少 typing 模块, 请安装: pip install typing")

__all__ = [
    "ZKFPMError", "DeviceError", "DeviceNotFoundError", "CaptureError",
    "MatchError", "MergeError", "ExtractError", "LibraryError", "raise_for",
]


class ZKFPMError(RuntimeError):
    """SDK 错误基类。``code`` 为原始 SDK 错误码 (0 成功, 其他见文档附录)。"""

    def __init__(self, code: int, message: Optional[str] = None) -> None:
        self.code = int(code)
        super(ZKFPMError, self).__init__(message or ("SDK 错误码 %d" % self.code))


class DeviceError(ZKFPMError):
    """设备层操作失败 (打开/关闭/参数读写)。"""


class DeviceNotFoundError(DeviceError):
    """无设备连接或打开设备失败 (-3/-6)。"""


class CaptureError(ZKFPMError):
    """取像失败 (-8/-12/-18/-28 等)。"""


class MatchError(ZKFPMError):
    """指纹比对不匹配 (-20)。"""


class MergeError(ZKFPMError):
    """登记模板合并失败 (-22)。"""


class ExtractError(ZKFPMError):
    """从图像提取模板失败 (-9/-26/-27)。"""


class LibraryError(ZKFPMError):
    """SDK 库初始化/其他底层失败 (-1/-2/-17 等)。"""


#: 错误码 -> 异常类 的映射 (未列出用 fallback)
_ERROR_CLASS_BY_CODE = {
    -3: DeviceNotFoundError,      # 无设备连接
    -6: DeviceNotFoundError,      # 打开设备失败
    -8: CaptureError,             # 取像失败
    -12: CaptureError,            # 设备正在采集
    -18: CaptureError,            # 取消采集
    -28: CaptureError,            # 超时
    -9: ExtractError,             # 提取模板失败
    -26: ExtractError,            # 文件打开失败
    -27: ExtractError,            # 处理图像失败
    -20: MatchError,              # 比对失败
    -22: MergeError,              # 合并失败
    -2: LibraryError,             # 初始化采集库失败
    -1: LibraryError,             # 初始化算法库失败
    -13: LibraryError,            # 添加模板失败
    -14: LibraryError,            # 删除模板失败
    -17: LibraryError,            # 操作失败
}


def error_class(code: int, fallback: type = ZKFPMError) -> type:
    """错误码对应的异常类 (供需要先判断再抛出的调用方使用)。"""
    return _ERROR_CLASS_BY_CODE.get(int(code), fallback)


def raise_for(code: int, message: Optional[str] = None, fallback: type = ZKFPMError) -> None:
    """SDK 返回非 0 时抛出对应异常; 0 时无事发生。"""
    if code != 0:
        raise error_class(code, fallback)(int(code), message)

# -*- coding: utf-8 -*-
"""ZKFinger Reader SDK (libzkfp.dll) 的 ctypes 绑定层。

低层封装: DLL 加载/定位、函数原型声明、错误码与参数代码枚举、
全局初始化引用计数。上层 (device/db) 只通过本模块访问 SDK。

语法兼容 Python 3.4+ (Windows XP): 禁用 f-string / 变量标注语法;
类型标注用内联标注 (PEP 3107) + typing 构造
(3.4 无内置 typing, 声明了 ``typing; python_version < "3.5"`` 依赖自动补齐)。
"""

from __future__ import absolute_import

import ctypes
import enum
import os

from .exceptions import ZKFPMError

try:
    from typing import Optional
except ImportError:  # pragma: no cover -- 精简环境缺 typing 时的提示
    raise ImportError("缺少 typing 模块, 请安装: pip install typing")

__all__ = [
    "ZKFPErr",
    "Param",
    "DBParam",
    "MAX_TEMPLATE_SIZE",
    "get_dll",
    "resolve_dll_path",
    "lib_addref",
    "lib_release",
]

#: 模板最大字节数 (libzkfptype.h)
MAX_TEMPLATE_SIZE = 2048

#: 覆盖 DLL 路径的环境变量名
DLL_ENV = "PYZK20R_DLL"

_DLL_NAME = "libzkfp.dll"


class ZKFPErr(enum.IntEnum):
    """SDK 错误码 (libzkfperrdef.h / SDK 文档附录 2)。"""

    OK = 0  # 操作成功
    ALREADY_INIT = 1  # 已经初始化 (视作成功)
    INITLIB = -1  # 初始化算法库失败
    INIT = -2  # 初始化采集库失败
    NO_DEVICE = -3  # 无设备连接
    NOT_SUPPORT = -4  # 接口暂不支持
    INVALID_PARAM = -5  # 无效参数
    OPEN = -6  # 打开设备失败
    INVALID_HANDLE = -7  # 无效句柄
    CAPTURE = -8  # 取像失败 (通常表示暂无手指)
    EXTRACT_FP = -9  # 提取指纹模板失败
    ABSORT = -10  # 中断
    MEMORY_NOT_ENOUGH = -11  # 内存不足
    BUSY = -12  # 当前正在采集
    ADD_FINGER = -13  # 添加指纹模板失败
    DEL_FINGER = -14  # 删除指纹模板失败
    FAIL = -17  # 操作失败
    CANCEL = -18  # 取消采集
    VERIFY_FP = -20  # 比对失败 (不匹配)
    MERGE = -22  # 合并登记模板失败
    NOT_OPENED = -23  # 设备未打开
    NOT_INIT = -24  # 未初始化
    ALREADY_OPENED = -25  # 设备已打开
    LOADIMAGE = -26  # 文件打开失败
    ANALYSE_IMG = -27  # 处理图像失败
    TIMEOUT = -28  # 超时


class Param(enum.IntEnum):
    """设备参数代码 (SDK 文档附录 1, 传给 ZKFPM_Get/SetParameters)。"""

    IMG_WIDTH = 1  # 只读 int  图像宽
    IMG_HEIGHT = 2  # 只读 int  图像高
    DPI = 3  # 读写 int  图像 DPI (Live20R 可写)
    IMG_SIZE = 106  # 只读 int  图像数据大小
    VENDOR = 101  # 只读 str  厂商信息
    PRODUCT_NAME = 102  # 只读 str  产品名
    SERIAL_NUMBER = 103  # 只读 str  设备序列号
    LED_WHITE = 104  # 只写 int  1 闪白灯 / 0 关闭
    LED_GREEN = 10001  # 只写 int  1 闪绿灯 / 0 关闭
    LED_RED = 10002  # 只写 int  1 闪红灯 / 0 关闭
    VID_PID = 1015  # 只读 4字节 VID&PID (前2字节VID, 后2字节PID)
    FAKE_SWITCH = 2002  # 读写 int  防假开关 (1 开 / 0 关)
    FAKE_STATUS = 2004  # 只读 int  低五位全 1 表示真手指 (value & 31 == 31)
    BEEP = 1101  # 只写 int  蜂鸣 (Live20R 不支持)
    TEMPLATE_FORMAT = 1102  # 读写 int  0 ANSI / 1 ISO (仅 ISO/ANSI 版本)
    TEMPLATE_QUALITY = 1103  # 只读 int  最近一次提取模板的质量
    DEVICE_STATUS = 50002  # 只读 int  0 异常 / 1 正常


class DBParam(enum.IntEnum):
    """算法库参数代码 (传给 ZKFPM_DBSet/GetParameter)。"""

    THRESHOLD_1_1 = 1  # 1:1 阈值, 默认 35, 范围 0~100
    THRESHOLD_1_N = 2  # 1:N 阈值, 默认 55, 范围 0~100


# ---------------------------------------------------------------------------
# DLL 加载
# ---------------------------------------------------------------------------


def resolve_dll_path(explicit: Optional[str] = None) -> str:
    """按 显式指定 -> 环境变量 -> 系统 的顺序定位 DLL。

    依赖系统副本: libzkfp.dll 的内部依赖 (ZKFPCap.dll -> fppswsk12.dll ->
    zkfinger12.dll 及 ZKFPSensors 插件目录) 按 libzkfp.dll 自身所在目录
    解析, 必须使用驱动安装的完整依赖链, 本库不单独分发 DLL。
    """
    if explicit:
        return explicit
    env = os.environ.get(DLL_ENV)
    if env:
        return env
    # 用绝对路径而非裸名: 旧版 ctypes 的搜索顺序包含应用目录和 CWD,
    # 裸名加载会被同名 DLL 劫持; 32 位 Python 下 WOW64 文件系统重定向
    # 会把 System32 字面路径自动映射到 SysWOW64, 行为一致且确定。
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", _DLL_NAME)
    if os.path.exists(system32):
        return system32
    return _DLL_NAME  # 兜底: 交给系统搜索 (SysWOW64/PATH)


def load_dll(path: Optional[str] = None) -> ctypes.WinDLL:
    """加载 libzkfp.dll 并声明全部函数原型 (stdcall)。"""
    dll = ctypes.WinDLL(resolve_dll_path(path))
    _set_prototypes(dll)
    return dll


#: 惰性加载的 DLL 单例; 测试可注入伪造对象
dll = None  # type: Optional[ctypes.WinDLL]


def get_dll(path: Optional[str] = None) -> ctypes.WinDLL:
    """返回已加载的 DLL, 必要时加载。

    首次加载后全局缓存, 再次调用时 ``path`` 被忽略 (先加载者优先);
    需要切换 DLL 的场景请在进程首次使用前设置 ``PYZK20R_DLL``。
    """
    global dll
    if dll is None:
        dll = load_dll(path)
    return dll


def _set_prototypes(dll: ctypes.WinDLL) -> None:
    """声明本库使用的 SDK 函数原型。仅绑定现行 DB* 接口, 旧别名不绑定。"""
    c_int = ctypes.c_int
    c_uint = ctypes.c_uint
    c_void_p = ctypes.c_void_p
    c_char_p = ctypes.c_char_p
    p_int = ctypes.POINTER(c_int)
    p_uint = ctypes.POINTER(c_uint)
    protos = {
        # 库 / 设备
        "ZKFPM_Init": ([], c_int),
        "ZKFPM_Terminate": ([], c_int),
        "ZKFPM_GetDeviceCount": ([], c_int),
        "ZKFPM_OpenDevice": ([c_int], c_void_p),
        "ZKFPM_CloseDevice": ([c_void_p], c_int),
        "ZKFPM_SetParameters": ([c_void_p, c_int, c_char_p, c_uint], c_int),
        "ZKFPM_GetParameters": ([c_void_p, c_int, c_char_p, p_uint], c_int),
        "ZKFPM_GetCaptureParamsEx": ([c_void_p, p_int, p_int, p_int], c_int),
        # 采集
        "ZKFPM_AcquireFingerprint": ([c_void_p, c_char_p, c_uint, c_char_p, p_uint], c_int),
        "ZKFPM_AcquireFingerprintImage": ([c_void_p, c_char_p, c_uint], c_int),
        # 算法库 (DBCache)
        "ZKFPM_DBInit": ([], c_void_p),
        "ZKFPM_DBFree": ([c_void_p], c_int),
        "ZKFPM_DBSetParameter": ([c_void_p, c_int, c_int], c_int),
        "ZKFPM_DBGetParameter": ([c_void_p, c_int, p_int], c_int),
        "ZKFPM_DBMerge": ([c_void_p, c_char_p, c_char_p, c_char_p, c_char_p, p_uint], c_int),
        "ZKFPM_DBAdd": ([c_void_p, c_uint, c_char_p, c_uint], c_int),
        "ZKFPM_DBDel": ([c_void_p, c_uint], c_int),
        "ZKFPM_DBClear": ([c_void_p], c_int),
        "ZKFPM_DBCount": ([c_void_p, p_uint], c_int),
        "ZKFPM_DBIdentify": ([c_void_p, c_char_p, c_uint, p_uint, p_uint], c_int),
        "ZKFPM_DBMatch": ([c_void_p, c_char_p, c_uint, c_char_p, c_uint], c_int),
        "ZKFPM_VerifyByID": ([c_void_p, c_uint, c_char_p, c_uint], c_int),
        # 辅助
        "ZKFPM_ExtractFromImage": ([c_void_p, c_char_p, c_uint, c_char_p, p_uint], c_int),
        "ZKFPM_GetTemplateQuality": ([c_void_p, c_char_p, c_int], c_int),
    }
    for name, (argtypes, restype) in protos.items():
        fn = getattr(dll, name)
        fn.argtypes = argtypes
        fn.restype = restype


# ---------------------------------------------------------------------------
# 全局初始化引用计数
# ---------------------------------------------------------------------------

#: ZKFPM_Init/ZKFPM_Terminate 的引用计数, 首次引用初始化, 归零释放
_init_refs = 0


def lib_addref(path: Optional[str] = None) -> ctypes.WinDLL:
    """确保 SDK 已初始化并递增引用计数, 返回 DLL。"""
    global _init_refs
    d = get_dll(path)
    if _init_refs == 0:
        ret = d.ZKFPM_Init()
        if ret not in (ZKFPErr.OK, ZKFPErr.ALREADY_INIT):
            raise ZKFPMError(int(ret), "ZKFPM_Init 失败")
    _init_refs += 1
    return d


def lib_release() -> None:
    """递减引用计数, 归零时释放 SDK 资源。"""
    global _init_refs
    if _init_refs > 0:
        _init_refs -= 1
        if _init_refs == 0:
            get_dll().ZKFPM_Terminate()

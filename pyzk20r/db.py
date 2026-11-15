# -*- coding: utf-8 -*-
"""算法库层: FingerprintDB (内存指纹库, 一人多指纹)。

SDK 内部以唯一 ``fid`` 存储每枚模板; 本层自动分配 fid 并维护
``user_id -> [fid...]`` 映射, 对外呈现"一个人可登记多枚指纹"的模型。

用法::

    from pyzk20r import Scanner, FingerprintDB

    db = FingerprintDB()
    with Scanner() as dev:
        db.enroll(dev, user_id=1001)           # 采 3 次合并登记 1 枚指纹
        db.enroll(dev, user_id=1001)           # 同一人再登记另一枚指纹
        uid, score = db.identify(dev.acquire().template)
        score = db.verify(1001, dev.acquire().template)

语法兼容 Python 3.4 (Windows XP): 参数/返回类型用内联标注 (PEP 3107),
typing 构造以字符串形式标注 (运行时不求值), 变量类型用注释。
"""
from __future__ import absolute_import

import ctypes

from . import _binding
from ._binding import MAX_TEMPLATE_SIZE, DBParam
from .device import Scanner
from .exceptions import LibraryError, MatchError, MergeError, error_class, raise_for

__all__ = ["FingerprintDB"]

#: 登记合并所需模板数 (ZKFPM_DBMerge 固定 3 枚)
MERGE_COUNT = 3

#: 字符串路径编码 (Windows ANSI, 兼容 XP)
_PATH_ENCODING = "mbcs"


class FingerprintDB:
    """内存指纹模板库 (SDK DBCache 封装), 支持一人多枚指纹。

    支持 with 上下文管理; 也可当纯算法库用 (不接设备也能比对模板)。
    """

    def __init__(self, dll_path: "str | None" = None) -> None:
        self._dll_path = dll_path
        self._handle = None                            # type: int | None
        handle = _binding.lib_addref(dll_path).ZKFPM_DBInit()
        if not handle:
            _binding.lib_release()
            raise LibraryError(-1, "创建算法库失败")
        self._handle = handle
        self._next_fid = 1
        self._fid_to_user = {}                         # type: dict
        self._user_fids = {}                           # type: dict
        self._fid_templates = {}                       # type: dict

    # -- 生命周期 -----------------------------------------------------------

    def close(self) -> None:
        """释放算法库。可安全重复调用。"""
        if self._handle is None:
            return
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBFree(self._handle)
        self._handle = None
        _binding.lib_release()
        raise_for(ret, "释放算法库失败")

    def __enter__(self) -> "FingerprintDB":
        return self

    def __exit__(self, exc_type: type, exc_val: BaseException, exc_tb: object) -> None:
        self.close()

    def _require_open(self) -> int:
        if self._handle is None:
            raise LibraryError(-7, "算法库已释放")
        return self._handle

    def __repr__(self) -> str:
        return "FingerprintDB(users=%d, templates=%d)" % (
            len(self._user_fids), len(self._fid_templates))

    # -- 登记 (一人多指纹) ---------------------------------------------------

    def add(self, user_id: int, template: bytes) -> None:
        """为 ``user_id`` 添加一枚指纹模板; 同一 user 可多次调用登记多枚。"""
        handle = self._require_open()
        fid = self._next_fid
        buf = ctypes.create_string_buffer(bytes(template), len(template))
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBAdd(handle, fid, buf, len(template))
        raise_for(ret, "添加指纹模板失败")
        self._next_fid += 1
        self._fid_to_user[fid] = user_id
        self._fid_templates[fid] = bytes(template)
        self._user_fids.setdefault(user_id, []).append(fid)

    def remove(self, user_id: int, template: "bytes | None" = None) -> None:
        """删除某用户的指纹; 传 ``template`` 只删匹配的那一枚, 否则全删。"""
        handle = self._require_open()
        target = bytes(template) if template is not None else None
        for fid in self._user_fids.get(user_id, [])[:]:
            if target is not None and self._fid_templates.get(fid) != target:
                continue
            ret = _binding.get_dll(self._dll_path).ZKFPM_DBDel(handle, fid)
            raise_for(ret, "删除指纹失败 (fid=%d)" % fid)
            del self._fid_to_user[fid]
            del self._fid_templates[fid]
            self._user_fids[user_id].remove(fid)
        if not self._user_fids.get(user_id):
            self._user_fids.pop(user_id, None)

    def clear(self) -> None:
        """清空全部指纹。"""
        handle = self._require_open()
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBClear(handle)
        raise_for(ret, "清空指纹库失败")
        self._fid_to_user.clear()
        self._fid_templates.clear()
        self._user_fids.clear()

    @property
    def count(self) -> int:
        """库内模板总数。"""
        handle = self._require_open()
        n = ctypes.c_uint()
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBCount(handle, ctypes.byref(n))
        raise_for(ret, "获取模板数失败")
        return n.value

    @property
    def users(self) -> "list[int]":
        """已登记的用户 ID 列表 (升序)。"""
        return sorted(self._user_fids)

    def fingers_of(self, user_id: int) -> "list[bytes]":
        """某用户已登记的全部模板 (bytes 列表)。"""
        return [self._fid_templates[f] for f in self._user_fids.get(user_id, [])]

    # -- 合并与登记流程 -------------------------------------------------------

    def merge(self, templates: "list[bytes]") -> bytes:
        """将 3 枚采集模板合并为 1 枚登记模板。"""
        if len(templates) != MERGE_COUNT:
            raise MergeError(-22, "合并需要恰好 %d 枚模板" % MERGE_COUNT)
        handle = self._require_open()
        bufs = [ctypes.create_string_buffer(bytes(t), len(t)) for t in templates]
        out = ctypes.create_string_buffer(MAX_TEMPLATE_SIZE)
        out_len = ctypes.c_uint(MAX_TEMPLATE_SIZE)
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBMerge(
            handle, bufs[0], bufs[1], bufs[2], out, ctypes.byref(out_len))
        raise_for(ret, "合并登记模板失败")
        return out.raw[:out_len.value]

    def enroll(self, scanner: Scanner, user_id: int, poll_interval: float = 0.1) -> bytes:
        """完整登记流程: 用 ``scanner`` 采 3 次 -> 合并 -> 存为 ``user_id`` 的指纹。

        返回登记模板。进度回调见 :meth:`pyzk20r.device.Scanner.on_enroll_progress`。
        """
        templates = scanner.enroll(poll_interval=poll_interval)
        reg = self.merge(templates)
        self.add(user_id, reg)
        return reg

    # -- 比对 -----------------------------------------------------------------

    def identify(self, template: bytes) -> "tuple[int, int]":
        """1:N 识别, 返回 ``(user_id, score)``; 无匹配抛 :class:`MatchError`。

        其他 SDK 错误 (-7/-17 等) 按原始错误码抛对应异常, 不误报为"无匹配"。
        """
        handle = self._require_open()
        buf = ctypes.create_string_buffer(bytes(template), len(template))
        fid = ctypes.c_uint()
        score = ctypes.c_uint()
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBIdentify(
            handle, buf, len(template), ctypes.byref(fid), ctypes.byref(score))
        if ret == -20:
            raise MatchError(-20, "1:N 识别无匹配")
        raise_for(ret, "识别失败")
        user_id = self._fid_to_user.get(fid.value)
        if user_id is None:
            raise MatchError(-20, "识别结果无法映射到用户 (fid=%d)" % fid.value)
        return user_id, score.value

    def verify(self, user_id: int, template: bytes) -> int:
        """1:1 验证: 对 ``user_id`` 的所有指纹取最优分; 不匹配抛 :class:`MatchError`。

        SDK 的 VerifyByID 只区分 ">0=分数 / 其他=失败", 失败可能是真不匹配
        (-20) 也可能是底层错误; 若所有指纹返回同一非 -20 负码则按该错误抛出。
        """
        handle = self._require_open()
        buf = ctypes.create_string_buffer(bytes(template), len(template))
        best = 0
        other_err = 0
        for fid in self._user_fids.get(user_id, []):
            ret = _binding.get_dll(self._dll_path).ZKFPM_VerifyByID(handle, fid, buf, len(template))
            if ret > best:
                best = ret
            elif ret < 0 and ret != -20 and not other_err:
                other_err = int(ret)
        if best > 0:
            return best
        if other_err:
            raise_for(other_err, "1:1 验证失败 (user_id=%r)" % (user_id,))
        raise MatchError(-20, "1:1 验证不匹配 (user_id=%r)" % (user_id,))

    def match(self, template_a: bytes, template_b: bytes) -> int:
        """直接比对两枚模板, 返回分数; 不匹配抛 :class:`MatchError`。"""
        handle = self._require_open()
        buf_a = ctypes.create_string_buffer(bytes(template_a), len(template_a))
        buf_b = ctypes.create_string_buffer(bytes(template_b), len(template_b))
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBMatch(
            handle, buf_a, len(template_a), buf_b, len(template_b))
        if ret > 0:
            return ret                               # 正数 = 匹配分数
        if ret == 0 or ret == -20:
            raise MatchError(-20, "模板比对不匹配")
        raise error_class(int(ret))(int(ret), "模板比对失败")   # 其他负码 = 底层错误

    # -- 阈值 -----------------------------------------------------------------

    @staticmethod
    def _get_threshold(handle: int, code: int) -> int:
        value = ctypes.c_int()
        ret = _binding.get_dll().ZKFPM_DBGetParameter(handle, int(code), ctypes.byref(value))
        raise_for(ret, "读取阈值失败")
        return value.value

    @property
    def threshold_1_1(self) -> int:
        """1:1 阈值 (默认 35, 范围 0~100)。"""
        return self._get_threshold(self._require_open(), DBParam.THRESHOLD_1_1)

    @threshold_1_1.setter
    def threshold_1_1(self, value: int) -> None:
        raise_for(_binding.get_dll(self._dll_path).ZKFPM_DBSetParameter(
            self._require_open(), int(DBParam.THRESHOLD_1_1), int(value)), "设置 1:1 阈值失败")

    @property
    def threshold_1_n(self) -> int:
        """1:N 阈值 (默认 55, 范围 0~100)。"""
        return self._get_threshold(self._require_open(), DBParam.THRESHOLD_1_N)

    @threshold_1_n.setter
    def threshold_1_n(self, value: int) -> None:
        raise_for(_binding.get_dll(self._dll_path).ZKFPM_DBSetParameter(
            self._require_open(), int(DBParam.THRESHOLD_1_N), int(value)), "设置 1:N 阈值失败")

    # -- 图像提取 ---------------------------------------------------------------

    def extract_from_image(self, path: str, dpi: int = 500) -> bytes:
        """从 BMP 图片提取指纹模板 (仅 SDK 标准版支持)。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(MAX_TEMPLATE_SIZE)
        out_len = ctypes.c_uint(MAX_TEMPLATE_SIZE)
        ret = _binding.get_dll(self._dll_path).ZKFPM_ExtractFromImage(
            handle, path.encode(_PATH_ENCODING), int(dpi), buf, ctypes.byref(out_len))
        raise_for(ret, "从图像提取模板失败")
        return buf.raw[:out_len.value]

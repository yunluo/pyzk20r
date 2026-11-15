# -*- coding: utf-8 -*-
"""算法库层: FingerprintDB (内存指纹库, 一人多指纹)。

SDK 内部以唯一 ``fid`` 存储每枚模板; 本层自动分配 fid 并维护
``user_id -> [fid...]`` 映射, 对外呈现"一个人可登记多枚指纹"的模型。

用法::

    from pyzk20r import Scanner, FingerprintDB

    db = FingerprintDB()
    with Scanner() as dev:
        db.enroll(dev, user_id="A001")         # 采 3 次合并登记 1 枚指纹
        db.enroll(dev, user_id="A001")         # 同一人再登记另一枚指纹
        uid, score = db.identify(dev.acquire().template)
        score = db.verify("A001", dev.acquire().template)

语法兼容 Python 3.4+ (Windows XP): 类型标注用内联标注 (PEP 3107) + typing 构造
(3.4 无内置 typing, 声明了 ``typing; python_version < "3.5"`` 依赖自动补齐)。
"""

from __future__ import absolute_import

import base64
import ctypes

from . import _binding
from ._binding import MAX_TEMPLATE_SIZE, DBParam
from .device import Scanner
from .exceptions import LibraryError, MatchError, MergeError, error_class, raise_for

try:
    from typing import List, Optional, Tuple, Union
except ImportError:  # pragma: no cover -- 精简环境缺 typing 时的提示
    raise ImportError("typing module not found, please install: pip install typing")

__all__ = ["FingerprintDB"]

#: 登记合并所需模板数 (ZKFPM_DBMerge 固定 3 枚)
MERGE_COUNT = 3

#: dump()/load() 持久化结构版本号 (v2: user_id 统一字符串; 兼容读取 v1 整数工号)
_DUMP_VERSION = 2
_DUMP_VERSIONS = (1, 2)

#: 字符串路径编码 (Windows ANSI, 兼容 XP)
_PATH_ENCODING = "mbcs"


def _norm_user_id(user_id: Union[str, int]) -> str:
    """工号归一化: 接受 str 或 int (bool 拒绝), 去首尾空白, 统一为 str。

    数字工号 ``52193652`` 与字符串 ``"52193652"`` 是同一工号。
    """
    if isinstance(user_id, bool) or not isinstance(user_id, (str, int)):
        raise TypeError("user_id must be str or int, got {}".format(type(user_id).__name__))
    text = str(user_id).strip()
    if not text:
        raise ValueError("user_id must not be empty")
    return text


def _check_template(template) -> bytes:
    """模板入参防御: 必须 bytes/bytearray、非空、不超过 MAX_TEMPLATE_SIZE。

    把"塞给 SDK 才报错"的晦涩失败提前到调用侧, 错误信息可直接定位。
    """
    if not isinstance(template, (bytes, bytearray)):
        raise TypeError("template must be bytes, got {}".format(type(template).__name__))
    data = bytes(template)
    if not data:
        raise ValueError("template must not be empty")
    if len(data) > MAX_TEMPLATE_SIZE:
        raise ValueError("template too large: {:d} bytes (max {:d})".format(len(data), MAX_TEMPLATE_SIZE))
    return data


class FingerprintDB:
    """内存指纹模板库 (SDK DBCache 封装), 支持一人多枚指纹。

    支持 with 上下文管理; 也可当纯算法库用 (不接设备也能比对模板)。
    """

    def __init__(self, dll_path: Optional[str] = None) -> None:
        self._dll_path = dll_path
        self._handle = None  # type: Optional[int]
        handle = _binding.lib_addref(dll_path).ZKFPM_DBInit()
        if not handle:
            _binding.lib_release()
            raise LibraryError(-1, "failed to create algorithm database")
        self._handle = handle
        self._next_fid = 1
        self._fid_to_user = {}  # type: dict
        self._user_fids = {}  # type: dict
        self._fid_templates = {}  # type: dict

    # -- 生命周期 -----------------------------------------------------------

    def close(self) -> None:
        """释放算法库。可安全重复调用。"""
        if self._handle is None:
            return
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBFree(self._handle)
        self._handle = None
        _binding.lib_release()
        raise_for(ret, "failed to free algorithm database")

    def __enter__(self):
        return self

    def __exit__(self, exc_type: type, exc_val: BaseException, exc_tb: object) -> None:
        self.close()

    def _require_open(self) -> int:
        if self._handle is None:
            raise LibraryError(-7, "algorithm database released")
        return self._handle

    def __repr__(self) -> str:
        return "FingerprintDB(users={:d}, templates={:d})".format(len(self._user_fids), len(self._fid_templates))

    # -- 登记 (一人多指纹) ---------------------------------------------------

    def add(self, user_id: Union[str, int], template: bytes) -> None:
        """为 ``user_id`` 添加一枚指纹模板; 同一工号可多次调用登记多枚。

        ``user_id`` 接受字符串或整数 (``"A00123"`` / ``52193652`` 均可),
        内部统一按字符串存储; fid 与 SDK uint 的对应关系由本层维护。
        """
        user_id = _norm_user_id(user_id)
        template = _check_template(template)
        handle = self._require_open()
        fid = self._next_fid
        buf = ctypes.create_string_buffer(template, len(template))
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBAdd(handle, fid, buf, len(template))
        raise_for(ret, "failed to add fingerprint template")
        self._next_fid += 1
        self._fid_to_user[fid] = user_id
        self._fid_templates[fid] = template
        self._user_fids.setdefault(user_id, []).append(fid)

    def remove(self, user_id: Union[str, int], template: Optional[bytes] = None) -> None:
        """删除某工号的指纹; 传 ``template`` 只删匹配的那一枚, 否则全删。"""
        user_id = _norm_user_id(user_id)
        handle = self._require_open()
        target = bytes(template) if template is not None else None
        for fid in self._user_fids.get(user_id, [])[:]:
            if target is not None and self._fid_templates.get(fid) != target:
                continue
            ret = _binding.get_dll(self._dll_path).ZKFPM_DBDel(handle, fid)
            raise_for(ret, "failed to delete fingerprint (fid={:d})".format(fid))
            del self._fid_to_user[fid]
            del self._fid_templates[fid]
            self._user_fids[user_id].remove(fid)
        if not self._user_fids.get(user_id):
            self._user_fids.pop(user_id, None)

    def clear(self) -> None:
        """清空全部指纹。"""
        handle = self._require_open()
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBClear(handle)
        raise_for(ret, "failed to clear fingerprint database")
        self._fid_to_user.clear()
        self._fid_templates.clear()
        self._user_fids.clear()

    @property
    def count(self) -> int:
        """库内模板总数。"""
        handle = self._require_open()
        n = ctypes.c_uint()
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBCount(handle, ctypes.byref(n))
        raise_for(ret, "failed to get template count")
        return n.value

    @property
    def users(self) -> List[str]:
        """已登记的工号列表 (字典序)。"""
        return sorted(self._user_fids)

    def fingers_of(self, user_id: Union[str, int]) -> List[bytes]:
        """某工号已登记的全部模板 (bytes 列表)。"""
        user_id = _norm_user_id(user_id)
        return [self._fid_templates[f] for f in self._user_fids.get(user_id, [])]

    # -- 持久化契约 (存储介质与时机由应用层决定) -------------------------------

    def dump(self) -> dict:
        """导出全部指纹为 JSON 兼容结构, 供应用层落盘 (json/sqlite/文件均可)。

        返回::

            {"version": 2,
             "templates": [[user_id, "BASE64模板"], ...]}

        与 :meth:`load` 配对使用 (load 兼容读取 v1 旧结构); 内存库本身不落盘,
        进程退出即消失。
        """
        return {
            "version": _DUMP_VERSION,
            "templates": [
                [uid, base64.b64encode(t).decode("ascii")] for uid in self.users for t in self.fingers_of(uid)
            ],
        }

    @classmethod
    def load(cls, data: dict):
        """从 :meth:`dump` 的结构重建内存指纹库 (一人多指纹关系原样恢复)。

        ``data`` 结构非法 (版本不符/字段缺失) 抛 :class:`ValueError`。
        """
        if not isinstance(data, dict):
            raise ValueError("dump data must be a dict")
        if data.get("version") not in _DUMP_VERSIONS:
            raise ValueError(
                "unsupported dump version: {!r} (supported: {})".format(data.get("version"), list(_DUMP_VERSIONS))
            )
        templates = data.get("templates")
        if not isinstance(templates, list):
            raise ValueError("'templates' field missing or not a list")
        db = cls()
        for item in templates:
            if not (isinstance(item, list) and len(item) == 2):
                raise ValueError("templates item must be [user_id, base64 string]")
            user_id, tpl_b64 = item
            try:
                template = base64.b64decode(tpl_b64, validate=True)
            except Exception as e:
                raise ValueError("template base64 decode failed: {}".format(e))
            template = _check_template(template)
            db.add(user_id, template)
        return db

    # -- 合并与登记流程 -------------------------------------------------------

    def merge(self, templates: List[bytes]) -> bytes:
        """将 3 枚采集模板合并为 1 枚登记模板。"""
        if len(templates) != MERGE_COUNT:
            raise MergeError(-22, "merge requires exactly {:d} templates".format(MERGE_COUNT))
        checked = [_check_template(t) for t in templates]
        handle = self._require_open()
        bufs = [ctypes.create_string_buffer(t, len(t)) for t in checked]
        out = ctypes.create_string_buffer(MAX_TEMPLATE_SIZE)
        out_len = ctypes.c_uint(MAX_TEMPLATE_SIZE)
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBMerge(
            handle, bufs[0], bufs[1], bufs[2], out, ctypes.byref(out_len)
        )
        raise_for(ret, "failed to merge enrollment templates")
        return out.raw[: out_len.value]

    def enroll(
        self,
        scanner: Scanner,
        user_id: Union[str, int],
        poll_interval: float = 0.1,
        timeout: Optional[float] = None,
    ) -> bytes:
        """完整登记流程: 用 ``scanner`` 采 3 次 -> 合并 -> 存为 ``user_id`` 的指纹。

        返回登记模板。进度回调见 :meth:`pyzk20r.device.Scanner.on_enroll_progress`。
        ``poll_interval`` / ``timeout`` 语义同 :meth:`Scanner.enroll`;
        ``timeout`` 到点抛 :class:`CaptureTimeoutError` (自助终端防挂死必传)。
        """
        templates = scanner.enroll(poll_interval=poll_interval, timeout=timeout)
        reg = self.merge(templates)
        self.add(user_id, reg)
        return reg

    # -- 比对 -----------------------------------------------------------------

    def identify(self, template: bytes) -> Tuple[str, int]:
        """1:N 识别, 返回 ``(user_id, score)``; 无匹配抛 :class:`MatchError`。

        其他 SDK 错误 (-7/-17 等) 按原始错误码抛对应异常, 不误报为"无匹配"。
        """
        template = _check_template(template)
        handle = self._require_open()
        buf = ctypes.create_string_buffer(template, len(template))
        fid = ctypes.c_uint()
        score = ctypes.c_uint()
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBIdentify(
            handle, buf, len(template), ctypes.byref(fid), ctypes.byref(score)
        )
        if ret == -20:
            raise MatchError(-20, "1:N identify: no match")
        raise_for(ret, "identify failed")
        user_id = self._fid_to_user.get(fid.value)
        if user_id is None:
            raise MatchError(-20, "identify result cannot map to user (fid={:d})".format(fid.value))
        return user_id, score.value

    def verify(self, user_id: Union[str, int], template: bytes) -> int:
        """1:1 验证: 对 ``user_id`` 的所有指纹取最优分; 不匹配抛 :class:`MatchError`。

        SDK 的 VerifyByID 只区分 ">0=分数 / 其他=失败", 失败可能是真不匹配
        (-20) 也可能是底层错误; 若所有指纹返回同一非 -20 负码则按该错误抛出。
        """
        template = _check_template(template)
        handle = self._require_open()
        buf = ctypes.create_string_buffer(template, len(template))
        user_id = _norm_user_id(user_id)
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
            raise_for(
                other_err,
                "1:1 verify failed (user_id={!r})".format(
                    user_id,
                ),
            )
        raise MatchError(
            -20,
            "1:1 verify: no match (user_id={!r})".format(
                user_id,
            ),
        )

    def match(self, template_a: bytes, template_b: bytes) -> int:
        """直接比对两枚模板, 返回分数; 不匹配抛 :class:`MatchError`。"""
        template_a = _check_template(template_a)
        template_b = _check_template(template_b)
        handle = self._require_open()
        buf_a = ctypes.create_string_buffer(template_a, len(template_a))
        buf_b = ctypes.create_string_buffer(template_b, len(template_b))
        ret = _binding.get_dll(self._dll_path).ZKFPM_DBMatch(handle, buf_a, len(template_a), buf_b, len(template_b))
        if ret > 0:
            return ret  # 正数 = 匹配分数
        if ret == 0 or ret == -20:
            raise MatchError(-20, "template match: no match")
        raise error_class(int(ret))(int(ret), "template match failed")  # 其他负码 = 底层错误

    # -- 阈值 -----------------------------------------------------------------

    @staticmethod
    def _get_threshold(handle: int, code: int) -> int:
        value = ctypes.c_int()
        ret = _binding.get_dll().ZKFPM_DBGetParameter(handle, int(code), ctypes.byref(value))
        raise_for(ret, "failed to get threshold")
        return value.value

    @property
    def threshold_1_1(self) -> int:
        """1:1 阈值 (默认 35, 范围 0~100)。"""
        return self._get_threshold(self._require_open(), DBParam.THRESHOLD_1_1)

    @threshold_1_1.setter
    def threshold_1_1(self, value: int) -> None:
        raise_for(
            _binding.get_dll(self._dll_path).ZKFPM_DBSetParameter(
                self._require_open(), int(DBParam.THRESHOLD_1_1), int(value)
            ),
            "failed to set 1:1 threshold",
        )

    @property
    def threshold_1_n(self) -> int:
        """1:N 阈值 (默认 55, 范围 0~100)。"""
        return self._get_threshold(self._require_open(), DBParam.THRESHOLD_1_N)

    @threshold_1_n.setter
    def threshold_1_n(self, value: int) -> None:
        raise_for(
            _binding.get_dll(self._dll_path).ZKFPM_DBSetParameter(
                self._require_open(), int(DBParam.THRESHOLD_1_N), int(value)
            ),
            "failed to set 1:N threshold",
        )

    # -- 图像提取 ---------------------------------------------------------------

    def extract_from_image(self, path: str, dpi: int = 500) -> bytes:
        """从 BMP 图片提取指纹模板 (仅 SDK 标准版支持)。"""
        handle = self._require_open()
        buf = ctypes.create_string_buffer(MAX_TEMPLATE_SIZE)
        out_len = ctypes.c_uint(MAX_TEMPLATE_SIZE)
        ret = _binding.get_dll(self._dll_path).ZKFPM_ExtractFromImage(
            handle, path.encode(_PATH_ENCODING), int(dpi), buf, ctypes.byref(out_len)
        )
        raise_for(ret, "failed to extract template from image")
        return buf.raw[: out_len.value]

# -*- coding: utf-8 -*-
"""会话层: FingerprintSession (采集-验证-灯效 一体化编排)。

面向工业终端这类"有人站在机器前"的场景: 等待手指时白灯引导, 验证通过
亮绿灯, 验证失败/超时/设备异常亮红灯, 全流程返回结果对象而不抛异常,
便于循环使用而不被异常打断。

用法::

    from pyzk20r import FingerprintDB, FingerprintSession, Scanner

    db = FingerprintDB()
    with Scanner() as dev:
        with FingerprintSession(db, dev) as session:
            result = session.wait_login(timeout=30)
            if result.ok:
                print("工号:", result.user_id)

本模块是**可选高阶封装**: 不使用它时, ``Scanner`` / ``FingerprintDB``
的行为完全不变。本层不做业务登录动作, 不落盘, 不含内部线程。

语法兼容 Python 3.4+ (Windows XP): 类型标注用内联标注 (PEP 3107) + typing
构造 (3.4 无内置 typing, 声明了 ``typing; python_version < "3.5"`` 依赖
自动补齐)。
"""

from __future__ import absolute_import

import time
from collections import namedtuple

from .device import Scanner
from .exceptions import CaptureTimeoutError, MatchError, ZKFPMError

try:
    from typing import Callable, Dict, Optional, Union
except ImportError:  # pragma: no cover -- 精简环境缺 typing 时的提示
    raise ImportError("typing module not found, please install: pip install typing")

__all__ = [
    "FingerprintSession",
    "SessionResult",
    "REASON_OK",
    "REASON_ENROLLED",
    "REASON_NOT_ENROLLED",
    "REASON_NO_MATCH",
    "REASON_TIMEOUT",
    "REASON_DEVICE_ERROR",
]

#: 操作成功
REASON_OK = "ok"

#: 登记成功
REASON_ENROLLED = "enrolled"

#: 库里没有登记任何指纹 (先注册, 后使用)
REASON_NOT_ENROLLED = "not_enrolled"

#: 采集到指纹但未匹配到任何已登记模板
REASON_NO_MATCH = "no_match"

#: 等待手指超时
REASON_TIMEOUT = "timeout"

#: 设备/算法库错误 (非匹配、非超时)
REASON_DEVICE_ERROR = "device_error"


class SessionResult(namedtuple("SessionResult", "ok reason user_id score")):
    """一次会话操作的结果。

    ``ok``      -- 是否成功;
    ``reason``  -- 原因常量 (见模块级 ``REASON_*``);
    ``user_id`` -- 识别/验证到的工号 (不适用时为 None);
    ``score``   -- 比对分数 (不适用时为 None)。
    """

    __slots__ = ()


class FingerprintSession:
    """采集-验证-灯效 一体化会话。

    组合已有的 :class:`Scanner` (设备) 与 :class:`FingerprintDB` (算法库),
    提供带灯效反馈的等待/验证流程。**不拥有** 这两个对象的所有权:
    :meth:`close` 只熄灭本会话点亮的灯, 不会关闭设备或算法库。

    灯效映射 (SDK 附录 1): 等待手指白灯常亮, 验证通过绿灯, 失败红灯。
    灯效是辅助反馈: 写入失败会静默降级, 绝不阻断验证主流程。

    ``on_event`` 可选回调 (或覆写 :meth:`on_event` 方法), 事件名见
    :data:`EVENTS`。
    """

    #: 结果灯 (绿/红) 显示秒数, 亮完自动熄灭回到等待态
    FLASH_SECONDS = 1.5

    #: 全部事件名 (回调的 ``event`` 取值)
    EVENTS = (
        "wait_start",  # 开始等待手指 (白灯亮)
        "captured",  # 采集到指纹
        "success",  # 验证通过 (绿灯)
        "failed",  # 验证失败/超时/设备异常 (红灯)
        "enroll_start",  # 开始登记
        "enroll_progress",  # 登记进度 (done/total)
        "enroll_done",  # 登记完成 (绿灯)
    )

    def __init__(
        self,
        db,
        scanner: Scanner,
        lights: bool = True,
        on_event: Optional[Callable[[str, Optional[Dict]], None]] = None,
    ) -> None:
        """
        ``db``        -- :class:`FingerprintDB` 实例 (提供模板与比对);
        ``scanner``  -- :class:`Scanner` 实例 (提供采集与灯效);
        ``lights``   -- 是否驱动灯效 (``False`` 则完全不写灯效参数);
        ``on_event`` -- 可选事件回调 ``callable(event, data)``;
                        为 ``None`` 时回退到可覆写的 :meth:`on_event` 方法。
        """
        self._db = db
        self._scanner = scanner
        self._lights = bool(lights)
        self._callback = on_event
        self._closed = False

    # -- 生命周期 -----------------------------------------------------------

    def close(self) -> None:
        """熄灭本会话可能点亮的灯。可安全重复调用。

        不关闭设备与算法库 (它们的所有权属于调用方)。
        """
        if self._closed:
            return
        self._closed = True
        for color in ("white", "green", "red"):
            self._set_light(color, False)

    def __enter__(self):
        return self

    def __exit__(self, exc_type: type, exc_val: BaseException, exc_tb: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "FingerprintSession(lights={}, closed={})".format(self._lights, self._closed)

    # -- 灯效 ---------------------------------------------------------------

    def _set_light(self, color: str, on: bool) -> None:
        """写灯效参数; 失败静默降级 (灯效是辅助反馈, 不能挡主流程)。"""
        if not self._lights:
            return
        try:
            self._scanner.led(color, on)
        except ZKFPMError:
            pass

    def _flash(self, color: str) -> None:
        """亮 ``color`` 灯 FLASH_SECONDS 秒后熄灭。"""
        if not self._lights:
            return
        self._set_light(color, True)
        if self.FLASH_SECONDS > 0:
            time.sleep(self.FLASH_SECONDS)
        self._set_light(color, False)

    # -- 事件 ---------------------------------------------------------------

    def emit(self, event: str, data: Optional[Dict] = None) -> None:
        """派发事件: 优先用构造时传入的回调, 否则交给 :meth:`on_event`。"""
        if self._callback is not None:
            self._callback(event, data)
        else:
            self.on_event(event, data)

    def on_event(self, event: str, data: Optional[Dict] = None) -> None:
        """事件回调钩子, 子类可覆写。事件名见 :data:`EVENTS`。"""

    # -- 登录 (1:N 识别 / 1:1 验证) --------------------------------------------

    def wait_login(
        self,
        timeout: Optional[float] = None,
        poll_interval: float = 0.15,
        user_id: Optional[Union[str, int]] = None,
    ) -> SessionResult:
        """等待手指 -> 采集 -> 比对 -> 灯效反馈。

        ``user_id`` 为 ``None`` 时走 1:N 识别 (返回工号); 给定工号时走
        1:1 验证 (返回分数, 工号即入参)。``timeout`` 为等待手指的最长
        秒数 (``None`` 表示一直等), 超时按失败处理 (红灯) 而非抛异常。

        全部失败路径都返回 :class:`SessionResult` (``ok=False``), 不抛
        异常——会话循环不应被一次失败打断。
        """
        if not self._db.users:
            # 先注册后使用: 库里没人, 提示红灯 (不是操作员的错)
            self._flash("red")
            self.emit("failed", {"reason": REASON_NOT_ENROLLED})
            return SessionResult(False, REASON_NOT_ENROLLED, None, None)

        self._set_light("white", True)
        self.emit("wait_start", {"timeout": timeout})
        try:
            frame = self._scanner.acquire(poll_interval=poll_interval, timeout=timeout)
        except CaptureTimeoutError as e:
            self._set_light("white", False)
            self._flash("red")
            self.emit("failed", {"reason": REASON_TIMEOUT, "error": e})
            return SessionResult(False, REASON_TIMEOUT, None, None)
        except ZKFPMError as e:
            self._set_light("white", False)
            self._flash("red")
            self.emit("failed", {"reason": REASON_DEVICE_ERROR, "error": e})
            return SessionResult(False, REASON_DEVICE_ERROR, None, None)
        self._set_light("white", False)
        self.emit("captured", {"size": len(frame.template)})

        try:
            if user_id is None:
                matched_id, score = self._db.identify(frame.template)
            else:
                score = self._db.verify(user_id, frame.template)
                matched_id = str(user_id)
        except MatchError as e:
            self._flash("red")
            self.emit("failed", {"reason": REASON_NO_MATCH, "error": e})
            return SessionResult(False, REASON_NO_MATCH, None, None)
        except ZKFPMError as e:
            self._flash("red")
            self.emit("failed", {"reason": REASON_DEVICE_ERROR, "error": e})
            return SessionResult(False, REASON_DEVICE_ERROR, None, None)

        self._flash("green")
        self.emit("success", {"user_id": matched_id, "score": score})
        return SessionResult(True, REASON_OK, matched_id, score)

    # -- 登记 -----------------------------------------------------------------

    def wait_enroll(
        self,
        user_id: Union[str, int],
        timeout: Optional[float] = None,
        poll_interval: float = 0.15,
    ) -> SessionResult:
        """等待手指 -> 采 3 次 -> 合并 -> 入库 -> 灯效反馈。

        成功返回 ``ok=True`` / ``reason=REASON_ENROLLED``; 失败路径与
        :meth:`wait_login` 一致 (红灯 + 结果对象, 不抛异常)。

        登记进度 (每次采集合并) 通过 ``enroll_progress`` 事件上报; 用户
        自己在 ``Scanner.on_enroll_progress`` 上挂的钩子仍会被调用。
        """
        self._set_light("white", True)
        self.emit("enroll_start", {"user_id": str(user_id)})
        original_hook = self._scanner.on_enroll_progress

        def _progress(done, total):
            self.emit("enroll_progress", {"done": done, "total": total})
            original_hook(done, total)

        self._scanner.on_enroll_progress = _progress
        try:
            template = self._db.enroll(
                self._scanner,
                user_id,
                poll_interval=poll_interval,
                timeout=timeout,
            )
        except CaptureTimeoutError as e:
            self._set_light("white", False)
            self._flash("red")
            self.emit("failed", {"reason": REASON_TIMEOUT, "error": e})
            return SessionResult(False, REASON_TIMEOUT, None, None)
        except ZKFPMError as e:
            self._set_light("white", False)
            self._flash("red")
            self.emit("failed", {"reason": REASON_DEVICE_ERROR, "error": e})
            return SessionResult(False, REASON_DEVICE_ERROR, None, None)
        finally:
            self._scanner.on_enroll_progress = original_hook
        self._set_light("white", False)
        self._flash("green")
        self.emit("enroll_done", {"user_id": str(user_id), "size": len(template)})
        return SessionResult(True, REASON_ENROLLED, str(user_id), None)

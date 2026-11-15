# -*- coding: utf-8 -*-
"""FingerprintSession 测试: 采集-验证-灯效 编排契约 (FakeDLL 注入, unittest)。"""

import unittest

import support  # noqa: F401  (引导 src 到 sys.path)
from support import FakeDLLTestCase, LEDRecordingDLL, UnsupportedLEDDLL

from pyzk20r import (
    REASON_DEVICE_ERROR,
    REASON_ENROLLED,
    REASON_NO_MATCH,
    REASON_NOT_ENROLLED,
    REASON_OK,
    REASON_TIMEOUT,
    FingerprintDB,
    FingerprintSession,
    Scanner,
    _binding,
)
from pyzk20r._binding import Param


class SessionTestCase(FakeDLLTestCase):
    """终端会话测试基类: 注入带 LED 记录的 FakeDLL, 关闭灯效等待时长。"""

    WHITE_ON = (int(Param.LED_WHITE), 1)
    WHITE_OFF = (int(Param.LED_WHITE), 0)
    GREEN = [(int(Param.LED_GREEN), 1), (int(Param.LED_GREEN), 0)]
    RED = [(int(Param.LED_RED), 1), (int(Param.LED_RED), 0)]

    def setUp(self):
        super(SessionTestCase, self).setUp()
        self.fake_dll = LEDRecordingDLL()
        _binding.dll = self.fake_dll
        self.scanner = Scanner()
        self.db = FingerprintDB()
        self.session = self._make_session()

    def tearDown(self):
        self.session.close()
        self.db.close()
        self.scanner.close()
        super(SessionTestCase, self).tearDown()

    def _make_session(self, **kwargs):
        """新建终端会话 (测试不等待灯效显示时长)。"""
        session = FingerprintSession(self.db, self.scanner, **kwargs)
        session.FLASH_SECONDS = 0
        return session

    def enroll_one(self, user_id="1001"):
        """往库里直接放一枚模板 (不触发灯效), 使 1:N 识别可命中。"""
        from support import TEMPLATE

        self.db.add(user_id, TEMPLATE)


class TestWaitLogin(SessionTestCase):
    """wait_login: 白灯引导 -> 采集 -> 比对 -> 绿/红灯。"""

    def test_login_success_green(self):
        """1:N 命中: 白灯亮起 -> 采集 -> 关白灯 -> 绿灯闪 -> 结果对象。"""
        self.enroll_one()
        result = self.session.wait_login(timeout=5, poll_interval=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, REASON_OK)
        self.assertEqual(result.user_id, "1001")
        self.assertEqual(result.score, 99)  # FakeDLL 默认 db_ident_result=(1, 99)
        self.assertEqual(
            self.fake_dll.led_writes,
            [self.WHITE_ON, self.WHITE_OFF] + self.GREEN,
        )

    def test_login_not_enrolled_red(self):
        """库里没人: 红灯 + not_enrolled, 不采集。"""
        result = self.session.wait_login(timeout=5, poll_interval=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_NOT_ENROLLED)
        self.assertIsNone(result.user_id)
        self.assertEqual(self.fake_dll.led_writes, self.RED)

    def test_login_no_match_red(self):
        """识别不到: 红灯 + no_match。"""
        self.enroll_one()
        self.fake_dll.db_ident_ret = -20
        result = self.session.wait_login(timeout=5, poll_interval=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_NO_MATCH)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.RED)

    def test_login_timeout_red(self):
        """等待手指超时: 红灯 + timeout, 不抛异常。"""
        self.enroll_one()
        self.fake_dll.acquire_fail = -8
        result = self.session.wait_login(timeout=0, poll_interval=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_TIMEOUT)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.RED)

    def test_login_device_error_red(self):
        """设备错误 (非匹配非超时): 红灯 + device_error。"""
        self.enroll_one()
        self.fake_dll.db_ident_ret = -17
        result = self.session.wait_login(timeout=5, poll_interval=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_DEVICE_ERROR)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.RED)

    def test_login_verify_1_1(self):
        """给定 user_id 时走 1:1 验证, 返回分数且工号即入参。"""
        self.enroll_one()
        self.fake_dll.verify_scores = {1: 80}
        result = self.session.wait_login(timeout=5, poll_interval=0, user_id="1001")
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, REASON_OK)
        self.assertEqual(result.user_id, "1001")
        self.assertEqual(result.score, 80)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.GREEN)

    def test_login_verify_1_1_no_match(self):
        """1:1 验证不匹配: 红灯 + no_match。"""
        self.enroll_one()
        self.fake_dll.verify_scores = {}  # 得分 0 -> 未匹配
        result = self.session.wait_login(timeout=5, poll_interval=0, user_id="1001")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_NO_MATCH)

    def test_login_led_failure_does_not_break_flow(self):
        """灯效写入失败 (机型无灯效): 主流程照常, 结果不受影响。"""
        _binding.dll = UnsupportedLEDDLL()
        self.fake_dll = _binding.dll
        self.enroll_one()
        result = self.session.wait_login(timeout=5, poll_interval=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.user_id, "1001")
        self.assertEqual(self.fake_dll.led_writes, [])

    def test_lights_disabled(self):
        """lights=False: 完全不写灯效参数, 流程正常。"""
        self.session.close()
        self.fake_dll.led_writes = []  # 丢弃上一个会话 close() 的灭灯记录
        self.session = self._make_session(lights=False)
        self.enroll_one()
        result = self.session.wait_login(timeout=5, poll_interval=0)
        self.assertTrue(result.ok)
        self.assertEqual(self.fake_dll.led_writes, [])


class TestWaitEnroll(SessionTestCase):
    """wait_enroll: 白灯引导 -> 采 3 次合并入库 -> 绿灯。"""

    def test_enroll_success_green(self):
        """登记成功: 白灯 -> 关白灯 -> 绿灯, 库里多一枚。"""
        result = self.session.wait_enroll("2001", timeout=5, poll_interval=0)
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, REASON_ENROLLED)
        self.assertEqual(result.user_id, "2001")
        self.assertEqual(self.db.count, 1)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.GREEN)

    def test_enroll_timeout_red(self):
        """登记超时: 红灯 + timeout, 不抛异常。"""
        self.fake_dll.acquire_fail = -8
        result = self.session.wait_enroll("2001", timeout=0, poll_interval=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_TIMEOUT)
        self.assertEqual(self.fake_dll.led_writes, [self.WHITE_ON, self.WHITE_OFF] + self.RED)
        self.assertEqual(self.db.count, 0)

    def test_enroll_progress_events(self):
        """登记过程派发 enroll_progress 事件 (done/total)。"""
        events = []
        payloads = []
        self.session.close()
        self.session = self._make_session(
            on_event=lambda e, d: (events.append(e), payloads.append(d)),
        )
        self.session.wait_enroll("2001", timeout=5, poll_interval=0)
        self.assertEqual(
            events,
            ["enroll_start", "enroll_progress", "enroll_progress", "enroll_progress", "enroll_done"],
        )
        self.assertEqual(payloads[1], {"done": 1, "total": 3})
        self.assertEqual(payloads[3], {"done": 3, "total": 3})

    def test_enroll_progress_keeps_user_hook(self):
        """用户在 Scanner.on_enroll_progress 上挂的钩子仍会被调用, 且用完即恢复。"""
        calls = []

        class ProgressScanner(Scanner):
            def on_enroll_progress(self, done, total):
                calls.append((done, total))

        self.session.close()
        self.scanner.close()
        self.scanner = ProgressScanner()
        self.session = self._make_session()
        self.session.wait_enroll("2001", timeout=5, poll_interval=0)
        self.assertEqual(calls, [(1, 3), (2, 3), (3, 3)])
        # 钩子恢复为原实现, 不残留包装
        self.assertIs(self.scanner.on_enroll_progress.__func__, ProgressScanner.on_enroll_progress)


class TestEvents(SessionTestCase):
    """事件回调钩子。"""

    def test_events_on_success(self):
        """成功路径事件序列: wait_start -> captured -> success。"""
        events = []
        self.session.close()
        self.session = self._make_session(on_event=lambda e, d: events.append(e))
        self.enroll_one()
        self.session.wait_login(timeout=5, poll_interval=0)
        self.assertEqual(events, ["wait_start", "captured", "success"])

    def test_events_on_failure(self):
        """失败路径事件: wait_start -> failed (带 reason)。"""
        events = []
        payloads = []
        self.session.close()
        self.session = self._make_session(
            on_event=lambda e, d: (events.append(e), payloads.append(d)),
        )
        self.enroll_one()
        self.fake_dll.db_ident_ret = -20
        self.session.wait_login(timeout=5, poll_interval=0)
        self.assertEqual(events, ["wait_start", "captured", "failed"])
        self.assertEqual(payloads[-1]["reason"], REASON_NO_MATCH)

    def test_on_event_override(self):
        """未传回调时回退到可覆写的 on_event 方法。"""

        class MySession(FingerprintSession):
            def __init__(self, *args, **kwargs):
                super(MySession, self).__init__(*args, **kwargs)
                self.events = []

            def on_event(self, event, data=None):
                self.events.append(event)

        session = MySession(self.db, self.scanner)
        session.FLASH_SECONDS = 0
        self.enroll_one()
        session.wait_login(timeout=5, poll_interval=0)
        self.assertEqual(session.events, ["wait_start", "captured", "success"])
        session.close()


class TestLifecycle(SessionTestCase):
    """生命周期: close 熄灭全部灯, 且不碰设备/算法库所有权。"""

    def test_close_turns_off_all_lights(self):
        """close() 熄灭白/绿/红三色灯, 可重复调用。"""
        self.enroll_one()
        self.session.wait_login(timeout=5, poll_interval=0)
        before = len(self.fake_dll.led_writes)
        self.session.close()
        writes = self.fake_dll.led_writes[before:]
        for color in (Param.LED_WHITE, Param.LED_GREEN, Param.LED_RED):
            self.assertIn((int(color), 0), writes)
        # 重复 close 不应再写
        count = len(self.fake_dll.led_writes)
        self.session.close()
        self.assertEqual(len(self.fake_dll.led_writes), count)

    def test_close_does_not_close_owned_resources(self):
        """close() 只关灯, 不关闭设备与算法库 (所有权属调用方)。"""
        self.session.close()
        self.assertFalse(self.scanner.closed)
        self.assertEqual(self.db.count, 0)  # 库仍可用

    def test_context_manager(self):
        """with 块退出时自动 close。"""
        with FingerprintSession(self.db, self.scanner) as session:
            self.assertFalse(session._closed)
        self.assertTrue(session._closed)


if __name__ == "__main__":
    unittest.main()

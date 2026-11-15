# -*- coding: utf-8 -*-
"""Scanner / Frame 设备层测试 (FakeDLL 注入, 无真设备, 标准库 unittest)。"""

import struct
import threading
import unittest

import support  # noqa: F401  (引导 src 到 sys.path)
from support import DPI, HEIGHT, IMAGE, TEMPLATE, WIDTH, FakeDLLTestCase

from pyzk20r import Scanner, ZKFPErr, ZKFPMError, _binding
from pyzk20r.device import Frame
from pyzk20r.exceptions import CaptureError, CaptureTimeoutError, DeviceNotFoundError


class RecordingScanner(Scanner):
    """记录进度钩子调用的子类, 验证继承点可用。"""

    def __init__(self, **kwargs):
        self.progress_calls = []
        super(RecordingScanner, self).__init__(**kwargs)

    def on_enroll_progress(self, done, total):
        self.progress_calls.append((done, total))


class TestDevice(FakeDLLTestCase):
    """设备层行为 (FakeDLL 注入)。"""

    def test_open_without_device(self):
        self.fake_dll.device_count = 0
        with self.assertRaises(DeviceNotFoundError):
            Scanner()

    def test_open_open_failure(self):
        self.fake_dll.open_ok = False
        with self.assertRaises(DeviceNotFoundError):
            Scanner()

    def test_context_manager_closes(self):
        with Scanner() as dev:
            self.assertFalse(dev.closed)
        self.assertTrue(dev.closed)
        self.assertEqual(self.fake_dll.term_calls, 1)

    def test_close_idempotent(self):
        dev = Scanner()
        dev.close()
        dev.close()
        self.assertEqual(self.fake_dll.term_calls, 1)

    def test_index_passed_to_open(self):
        dev = Scanner(index=2)
        self.assertEqual(self.fake_dll.opened_index, 2)
        dev.close()

    def test_params(self):
        with Scanner() as dev:
            self.assertEqual(dev.width, WIDTH)
            self.assertEqual(dev.height, HEIGHT)
            self.assertEqual(dev.dpi, DPI)
            self.assertIs(dev.device_ok, True)
            self.assertIs(dev.fake_detect, True)
            self.assertIs(dev.finger_is_live, True)

    def test_set_param_int(self):
        with Scanner() as dev:
            dev.dpi = 1000
            dev.fake_detect = False
        self.assertEqual(struct.unpack("<i", self.fake_dll.set_params[3]), (1000,))
        self.assertEqual(struct.unpack("<i", self.fake_dll.set_params[2002]), (0,))

    def test_template_format(self):
        with Scanner() as dev:
            self.assertEqual(dev.template_format, "ansi")
            dev.template_format = "iso"
            self.assertEqual(dev.template_format, "iso")
            with self.assertRaises(ValueError):
                dev.template_format = "xyz"

    def test_led(self):
        """灯效参数码: 101 白 / 102 绿 / 103 红 (SDK 附录 1)。"""
        with Scanner() as dev:
            dev.led("green", True)
            dev.led("red", False)
            with self.assertRaises(ValueError):
                dev.led("blue")
        self.assertEqual(struct.unpack("<i", self.fake_dll.set_params[102]), (1,))
        self.assertEqual(struct.unpack("<i", self.fake_dll.set_params[103]), (0,))

    def test_info(self):
        with Scanner() as dev:
            info = dev.info()
        self.assertEqual(info.vendor, "ZKTeco Inc")
        self.assertEqual(info.product, "Live20R")
        self.assertEqual(info.serial, "SN12345678")
        self.assertEqual(info.vid_pid, "VID_1B55 PID_0223")

    def test_acquire_returns_frame(self):
        with Scanner() as dev:
            frame = dev.acquire()
        self.assertIsInstance(frame, Frame)
        self.assertEqual(frame.template, TEMPLATE)
        self.assertEqual(frame.width, WIDTH)
        self.assertEqual(frame.height, HEIGHT)
        self.assertEqual(frame.dpi, DPI)
        self.assertEqual(frame.image[:10], IMAGE[:10])

    def test_acquire_timeout_raises_capture_timeout(self):
        """超时抛 CaptureTimeoutError (timeout=0 即立即超时)。"""
        self.fake_dll.acquire_queue = [-8, -8, -8]
        with Scanner() as dev:
            with self.assertRaises(CaptureTimeoutError) as cm:
                dev.acquire(poll_interval=0, timeout=0)
        # 向后兼容契约: CaptureTimeoutError 是 CaptureError 子类, 旧分支不受影响
        self.assertIsInstance(cm.exception, CaptureError)
        self.assertEqual(cm.exception.code, int(ZKFPErr.CAPTURE))

    def test_acquire_waits_for_finger(self):
        """阻塞等待语义: 前两次失败 (暂无手指), 第三次成功。"""
        self.fake_dll.acquire_queue = [-8, -8, 0]
        with Scanner() as dev:
            frame = dev.acquire(poll_interval=0)
        self.assertEqual(frame.template, TEMPLATE)

    def test_capture_generator_retries_transient(self):
        self.fake_dll.acquire_queue = [-8, 0]  # 一次失败后成功
        with Scanner() as dev:
            gen = dev.capture(poll_interval=0)
            frame = next(gen)
            self.assertEqual(frame.template, TEMPLATE)
            gen.close()

    def test_try_acquire_override_hook(self):
        """公开扩展点: 子类覆写 try_acquire (原 _try_acquire) 可拦截采集。"""
        calls = []

        class HookScanner(Scanner):
            def try_acquire(self, params=None):
                calls.append(params is not None)
                return super(HookScanner, self).try_acquire(params)

        dev = HookScanner()
        frame = dev.acquire(poll_interval=0)
        dev.close()
        self.assertEqual(frame.template, TEMPLATE)
        self.assertEqual(calls, [True])

    def test_acquire_image(self):
        with Scanner() as dev:
            img = dev.acquire_image()
        self.assertEqual(len(img), WIDTH * HEIGHT)
        self.assertEqual(img, IMAGE)

    def test_enroll_collects_three_and_calls_hook(self):
        dev = RecordingScanner()
        templates = dev.enroll(poll_interval=0)
        dev.close()
        self.assertEqual(len(templates), 3)
        for t in templates:
            self.assertEqual(t, TEMPLATE)
        self.assertEqual(dev.progress_calls, [(1, 3), (2, 3), (3, 3)])

    def test_enroll_no_times_parameter(self):
        """enroll 固定采 3 次, 不再接受 times 参数 (SDK 合并要求)。"""
        dev = Scanner()
        with self.assertRaises(TypeError):
            dev.enroll(times=5)  # type: ignore[call-arg]
        dev.close()

    def test_operations_on_closed_device_raise(self):
        dev = Scanner()
        dev.close()
        with self.assertRaises(ZKFPMError):
            dev.get_param_int(1)
        with self.assertRaises(ZKFPMError):
            dev.acquire()

    def test_template_quality(self):
        with Scanner() as dev:
            self.assertEqual(dev.template_quality(TEMPLATE), 85)

    def test_multi_instance_refcount(self):
        """两个设备共享一次 Init; 关一个不 Terminate, 全关才 Terminate。"""
        dev1 = Scanner()
        dev2 = Scanner()
        self.assertEqual(self.fake_dll.init_calls, 1)
        dev1.close()
        self.assertEqual(self.fake_dll.term_calls, 0)
        dev2.close()
        self.assertEqual(self.fake_dll.term_calls, 1)

    def test_reopen_after_close(self):
        dev = Scanner()
        dev.close()
        dev.open()
        self.assertFalse(dev.closed)
        self.assertEqual(self.fake_dll.opened_index, 0)
        dev.close()

    def test_frame_to_bmp_layout(self):
        frame = Frame(IMAGE, TEMPLATE, WIDTH, HEIGHT, DPI)
        bmp = frame.to_bmp()
        self.assertEqual(bmp[:2], b"BM")
        file_size, _res1, _res2, offset = struct.unpack_from("<IHHI", bmp, 2)
        self.assertEqual(offset, 14 + 40 + 1024)
        self.assertEqual(file_size, len(bmp))
        self.assertEqual(len(bmp), offset + (WIDTH + (4 - WIDTH % 4) % 4) * HEIGHT)
        bi_size, bi_w, bi_h, planes, bpp = struct.unpack_from("<IiiHH", bmp, 14)
        self.assertEqual((bi_size, bi_w, bi_h, planes, bpp), (40, WIDTH, HEIGHT, 1, 8))

    def test_frame_to_bmp_bottom_up(self):
        """最后一行像素在 BMP 头之后最先出现 (自底向上)。"""
        image = bytes(range(16))  # 4x4 图
        frame = Frame(image, TEMPLATE, 4, 4, 500)
        bmp = frame.to_bmp()
        body = bmp[14 + 40 + 1024 :]
        self.assertEqual(body[:4], image[12:16])  # 第 3 行 (最底行) 在最前
        self.assertEqual(body[-4:], image[0:4])  # 第 0 行 (最顶行) 在最后

    def test_error_code_preserved(self):
        self.fake_dll.device_count = 0
        with self.assertRaises(DeviceNotFoundError) as cm:
            Scanner()
        self.assertEqual(cm.exception.code, int(ZKFPErr.NO_DEVICE))

    def test_enroll_timeout_raises(self):
        """无人值守防线: 登记超时抛 CaptureTimeoutError, 不无限阻塞。"""
        self.fake_dll.acquire_queue = [-8] * 10
        dev = RecordingScanner()
        with self.assertRaises(CaptureTimeoutError):
            dev.enroll(poll_interval=0, timeout=0)
        dev.close()

    def test_enroll_timeout_partial_progress(self):
        """采到 2 枚后超时: 抛 CaptureTimeoutError, 已采模板不部分返回。"""
        self.fake_dll.acquire_queue = [0, 0, -8, -8]
        dev = RecordingScanner()
        with self.assertRaises(CaptureTimeoutError):
            dev.enroll(poll_interval=0, timeout=0)
        self.assertEqual(dev.progress_calls, [(1, 3), (2, 3)])
        dev.close()

    def test_enroll_success_ignores_timeout_zero(self):
        """登记流程一次成功就不受 timeout 影响 (超时只在等待时生效)。"""
        self.fake_dll.acquire_queue = [0, 0, 0]
        dev = Scanner()
        templates = dev.enroll(poll_interval=0, timeout=0)
        dev.close()
        self.assertEqual(len(templates), 3)

    def test_multithread_open_close_refcount(self):
        """多线程并发构造/关闭: 引用计数加锁后不泄漏、不双重 Terminate。

        所有线程先打开 40 个设备 (屏障同步), 峰值共存期间必须只 Init 一次;
        统一关闭后归零且只 Terminate 一次。
        """
        errors = []
        barrier = threading.Barrier(8)

        def worker():
            devs = []
            try:
                barrier.wait(10)
                for _ in range(5):
                    devs.append(Scanner())
                barrier.wait(10)  # 全部打开后才允许关闭
                for dev in devs:
                    dev.close()
            except Exception as e:  # pragma: no cover - 失败会被断言捕获
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        self.assertEqual(errors, [])
        self.assertEqual(self.fake_dll.init_calls, 1)  # 峰值共存只 Init 一次
        self.assertEqual(self.fake_dll.term_calls, 1)  # 全关才 Terminate 一次
        self.assertEqual(_binding._init_refs, 0)


if __name__ == "__main__":
    unittest.main()

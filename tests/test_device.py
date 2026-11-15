# -*- coding: utf-8 -*-
"""Scanner / Frame 设备层测试 (FakeDLL 注入, 无真设备)。"""

import struct

import pytest
from conftest import DPI, HEIGHT, IMAGE, TEMPLATE, WIDTH

from pyzk20r import Scanner, ZKFPErr, ZKFPMError
from pyzk20r.device import Frame
from pyzk20r.exceptions import CaptureError, DeviceNotFoundError


class RecordingScanner(Scanner):
    """记录进度钩子调用的子类, 验证继承点可用。"""

    def __init__(self, **kwargs):
        self.progress_calls = []
        super(RecordingScanner, self).__init__(**kwargs)

    def on_enroll_progress(self, done, total):
        self.progress_calls.append((done, total))


def test_open_without_device(fake_dll):
    fake_dll.device_count = 0
    with pytest.raises(DeviceNotFoundError):
        Scanner()


def test_open_open_failure(fake_dll):
    fake_dll.open_ok = False
    with pytest.raises(DeviceNotFoundError):
        Scanner()


def test_context_manager_closes(fake_dll):
    with Scanner() as dev:
        assert not dev.closed
    assert dev.closed
    assert fake_dll.term_calls == 1


def test_close_idempotent(fake_dll):
    dev = Scanner()
    dev.close()
    dev.close()
    assert fake_dll.term_calls == 1


def test_index_passed_to_open(fake_dll):
    dev = Scanner(index=2)
    assert fake_dll.opened_index == 2
    dev.close()


def test_params(fake_dll):
    with Scanner() as dev:
        assert dev.width == WIDTH
        assert dev.height == HEIGHT
        assert dev.dpi == DPI
        assert dev.device_ok is True
        assert dev.fake_detect is True
        assert dev.finger_is_live is True


def test_set_param_int(fake_dll):
    with Scanner() as dev:
        dev.dpi = 1000
        dev.fake_detect = False
    assert struct.unpack("<i", fake_dll.set_params[3]) == (1000,)
    assert struct.unpack("<i", fake_dll.set_params[2002]) == (0,)


def test_template_format(fake_dll):
    with Scanner() as dev:
        assert dev.template_format == "ansi"
        dev.template_format = "iso"
        assert dev.template_format == "iso"
        with pytest.raises(ValueError):
            dev.template_format = "xyz"


def test_led(fake_dll):
    with Scanner() as dev:
        dev.led("green", True)
        dev.led("red", False)
        with pytest.raises(ValueError):
            dev.led("blue")
    assert struct.unpack("<i", fake_dll.set_params[10001]) == (1,)
    assert struct.unpack("<i", fake_dll.set_params[10002]) == (0,)


def test_info(fake_dll):
    with Scanner() as dev:
        info = dev.info()
    assert info.vendor == "ZKTeco Inc"
    assert info.product == "Live20R"
    assert info.serial == "SN12345678"
    assert info.vid_pid == "VID_1B55 PID_0223"


def test_acquire_returns_frame(fake_dll):
    with Scanner() as dev:
        frame = dev.acquire()
    assert isinstance(frame, Frame)
    assert frame.template == TEMPLATE
    assert frame.width == WIDTH
    assert frame.height == HEIGHT
    assert frame.dpi == DPI
    assert frame.image[:10] == IMAGE[:10]


def test_acquire_raises_on_persistent_fail(fake_dll):
    """超时后抛 CaptureError (timeout=0 即立即超时)。"""
    fake_dll.acquire_queue = [-8, -8, -8]
    with Scanner() as dev:
        with pytest.raises(CaptureError):
            dev.acquire(poll_interval=0, timeout=0)


def test_acquire_waits_for_finger(fake_dll):
    """阻塞等待语义: 前两次失败 (暂无手指), 第三次成功。"""
    fake_dll.acquire_queue = [-8, -8, 0]
    with Scanner() as dev:
        frame = dev.acquire(poll_interval=0)
    assert frame.template == TEMPLATE


def test_capture_generator_retries_transient(fake_dll):
    fake_dll.acquire_queue = [-8, 0]  # 一次失败后成功
    with Scanner() as dev:
        gen = dev.capture(poll_interval=0)
        frame = next(gen)
        assert frame.template == TEMPLATE
        gen.close()


def test_acquire_image(fake_dll):
    with Scanner() as dev:
        img = dev.acquire_image()
    assert len(img) == WIDTH * HEIGHT
    assert img == IMAGE


def test_enroll_collects_three_and_calls_hook(fake_dll):
    dev = RecordingScanner()
    templates = dev.enroll(poll_interval=0)
    dev.close()
    assert len(templates) == 3
    assert all(t == TEMPLATE for t in templates)
    assert dev.progress_calls == [(1, 3), (2, 3), (3, 3)]


def test_enroll_no_times_parameter(fake_dll):
    """enroll 固定采 3 次, 不再接受 times 参数 (SDK 合并要求)。"""
    dev = Scanner()
    with pytest.raises(TypeError):
        dev.enroll(times=5)  # type: ignore[call-arg]
    dev.close()


def test_operations_on_closed_device_raise(fake_dll):
    dev = Scanner()
    dev.close()
    with pytest.raises(ZKFPMError):
        dev.get_param_int(1)
    with pytest.raises(ZKFPMError):
        dev.acquire()


def test_template_quality(fake_dll):
    with Scanner() as dev:
        assert dev.template_quality(TEMPLATE) == 85


def test_multi_instance_refcount(fake_dll):
    """两个设备共享一次 Init; 关一个不 Terminate, 全关才 Terminate。"""
    dev1 = Scanner()
    dev2 = Scanner()
    assert fake_dll.init_calls == 1
    dev1.close()
    assert fake_dll.term_calls == 0
    dev2.close()
    assert fake_dll.term_calls == 1


def test_reopen_after_close(fake_dll):
    dev = Scanner()
    dev.close()
    dev.open()
    assert not dev.closed
    assert fake_dll.opened_index == 0
    dev.close()


def test_frame_to_bmp_layout(fake_dll):
    frame = Frame(IMAGE, TEMPLATE, WIDTH, HEIGHT, DPI)
    bmp = frame.to_bmp()
    assert bmp[:2] == b"BM"
    file_size, _res1, _res2, offset = struct.unpack_from("<IHHI", bmp, 2)
    assert offset == 14 + 40 + 1024
    assert file_size == len(bmp) == offset + (WIDTH + (4 - WIDTH % 4) % 4) * HEIGHT
    bi_size, bi_w, bi_h, planes, bpp = struct.unpack_from("<IiiHH", bmp, 14)
    assert (bi_size, bi_w, bi_h, planes, bpp) == (40, WIDTH, HEIGHT, 1, 8)


def test_frame_to_bmp_bottom_up(fake_dll):
    """最后一行像素在 BMP 头之后最先出现 (自底向上)。"""
    image = bytes(range(16))  # 4x4 图
    frame = Frame(image, TEMPLATE, 4, 4, 500)
    bmp = frame.to_bmp()
    body = bmp[14 + 40 + 1024 :]
    assert body[:4] == image[12:16]  # 第 3 行 (最底行) 在最前
    assert body[-4:] == image[0:4]  # 第 0 行 (最顶行) 在最后


def test_error_code_preserved(fake_dll):
    fake_dll.device_count = 0
    with pytest.raises(DeviceNotFoundError) as ei:
        Scanner()
    assert ei.value.code == int(ZKFPErr.NO_DEVICE)

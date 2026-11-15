# -*- coding: utf-8 -*-
"""采集一帧指纹并保存为 BMP 图片。

用法: python capture.py [输出路径.bmp]
"""
import sys

from pyzk20r import Scanner


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "fingerprint.bmp"
    with Scanner() as dev:
        print("设备: %s %s 序列号=%s" % (dev.info().vendor, dev.info().product, dev.info().serial))
        print("等待手指... (图像 %dx%d @%d DPI, 防假=%s)" % (
            dev.width, dev.height, dev.dpi, dev.fake_detect))
        for frame in dev.capture(poll_interval=0.1):
            with open(out, "wb") as f:
                f.write(frame.to_bmp())
            print("已保存 %s (图像 %d 字节, 模板 %d 字节)"
                  % (out, len(frame.image), len(frame.template)))
            break


if __name__ == "__main__":
    main()

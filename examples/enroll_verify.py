# -*- coding: utf-8 -*-
"""登记 + 1:1 验证演示。

给工号 1001 连续登记两枚指纹, 然后现场采集并用 1:1 验证身份。
"""
from pyzk20r import FingerprintDB, MatchError, Scanner

USER = "1001"


def main():
    db = FingerprintDB()
    with Scanner() as dev:
        dev.fake_detect = True
        for i in range(2):
            print("[%d/2] 请按压手指 3 次..." % (i + 1))
            tpl = db.enroll(dev, USER)
            print("  登记完成, 登记模板 %d 字节" % len(tpl))

        print("请再次按压手指进行 1:1 验证...")
        try:
            score = db.verify(USER, dev.acquire().template)
            print("验证通过, 分数 %d (阈值 %d)" % (score, db.threshold_1_1))
        except MatchError as e:
            print("验证失败: %s" % e)
    db.close()


if __name__ == "__main__":
    main()

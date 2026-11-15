# -*- coding: utf-8 -*-
"""多人登记 + 1:N 识别演示 (一人多枚指纹)。

登记 3 个用户后进入识别循环, 按 Ctrl+C 退出。
"""
from pyzk20r import FingerprintDB, MatchError, Scanner

USERS = ["1001", "1002", "1003"]


def main():
    db = FingerprintDB()
    with Scanner() as dev:
        for uid in USERS:
            print("工号 %s: 请按压手指 3 次..." % uid)
            db.enroll(dev, uid)
            print("  已登记 (当前模板总数 %d)" % db.count)

        print("进入识别循环, Ctrl+C 退出")
        try:
            for frame in dev.capture(poll_interval=0.1):
                try:
                    uid, score = db.identify(frame.template)
                    print("识别成功: 工号 %s, 分数 %d (阈值 %d)" % (uid, score, db.threshold_1_n))
                except MatchError:
                    print("未识别 (无匹配)")
        except KeyboardInterrupt:
            print("\n退出")
    db.close()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""工业现场登录改造参考实现: 指纹 1:N 识别自动登录 (工号 = user_id)。

场景: 制造业工业软件原"工号+密码"登录, 改造为登记指纹后刷指纹自动登录。

- 管理端 (班组长/管理员操作): 登记工号 + 指纹 (每人可多枚), 存 SQLite
- 登录端 (员工操作): 按指纹 -> identify 返回工号 -> 自动登录
- 灯效反馈: 登记成功/登录成功亮绿灯, 登录失败或采集失败亮红灯
  (灯效依赖固件支持, 不支持时自动降级为纯文字反馈; 采像白灯由固件
  在采集时自动点亮, 应用层只管结果灯)
- 兜底: 设备缺失/手指受伤时, 保留原工号+密码登录

对接你自己的工业软件: 只需实现 ``do_login(工号)`` (弹窗/HTTP/MES API 皆可);
工号为任意字符串 (如 ``"A00123"``), 直接作为 user_id 使用。
"""

import sqlite3
import sys
import time

from pyzk20r import FingerprintDB, MatchError, Scanner, ZKFPMError

DB_FILE = "workers.db"

#: 登录端等待手指的超时秒数 (无人值守防挂死, 到点抛 CaptureTimeoutError)
LOGIN_TIMEOUT = 60

#: 结果灯效显示秒数 (绿/红灯亮多久后熄灭)
FLASH_SECONDS = 1.5


# ============ 灯效反馈: 登记成功/登录成功绿灯, 失败红灯 ============


def flash_led(dev, color):
    # type: (Scanner, str) -> None
    """亮 ``color`` 灯 FLASH_SECONDS 秒后熄灭, 作为一次操作的结果反馈。

    灯效是辅助反馈, 不能挡登录主流程: 部分固件/硬件变体没有可编程
    三色灯 (实测 VID_1B55 PID_0120: green 静默无效, red 直接抛
    DeviceError), 失败时静默降级为纯文字反馈。
    """
    try:
        dev.led(color)
        time.sleep(FLASH_SECONDS)
        dev.led(color, False)
    except ZKFPMError:
        pass  # 该设备无灯效硬件, 控制台文字输出已是主反馈


# ============ 对接点: 换成你的工业软件登录调用 ============


def do_login(worker_id):
    # type: (str) -> None
    """识别成功后的自动登录动作, 把这里换成你的工业软件登录 API。"""
    print(">>> 自动登录: 工号 {} (此处调用你的工业软件)".format(worker_id))


def check_password(worker_id, password):
    # type: (str, str) -> bool
    """兜底登录的口令校验, 对接你原来的工号+密码逻辑。示例恒真。"""
    return True  # TODO: 接你原来的密码表/哈希校验


# ============ 应用侧存储: 员工 + 指纹模板 (SQLite) ============


def open_store():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS workers (worker_id TEXT PRIMARY KEY, password TEXT NOT NULL DEFAULT '')")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fingerprints ("
        "worker_id TEXT NOT NULL, seq INTEGER NOT NULL, tpl BLOB NOT NULL,"
        " PRIMARY KEY (worker_id, seq))"
    )
    return conn


def load_db(conn):
    """启动时从 SQLite 重建内存指纹库 (识别用)。"""
    db = FingerprintDB()
    for worker_id, tpl in conn.execute("SELECT worker_id, tpl FROM fingerprints ORDER BY worker_id, seq"):
        db.add(worker_id, tpl)
    return db


def save_fingerprints(conn, worker_id, templates):
    conn.execute("DELETE FROM fingerprints WHERE worker_id=?", (worker_id,))
    conn.executemany(
        "INSERT INTO fingerprints (worker_id, seq, tpl) VALUES (?, ?, ?)",
        [(worker_id, seq, tpl) for seq, tpl in enumerate(templates)],
    )
    conn.commit()


# ============ 管理端: 登记 ============


def register_worker(conn, db, dev):
    worker_id = input("输入工号: ").strip()
    if not worker_id:
        return
    row = conn.execute("SELECT 1 FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO workers (worker_id) VALUES (?)", (worker_id,))
        conn.commit()
        print("员工 {} 已登记, 请依次按压手指".format(worker_id))
    else:
        print("员工 {} 已存在, 追加一枚新指纹".format(worker_id))

    while True:
        print("第 {:d} 枚指纹: 请按压手指 3 次...".format(len(db.fingers_of(worker_id)) + 1))
        db.enroll(dev, user_id=worker_id)  # 采 3 次 -> DBMerge -> 入库
        save_fingerprints(conn, worker_id, db.fingers_of(worker_id))
        flash_led(dev, "green")  # 登记成功绿灯
        print("已保存 (该员工共 {:d} 枚)".format(len(db.fingers_of(worker_id))))
        again = input("继续加录一枚? (y/N): ").strip().lower()
        if again != "y":
            break


def remove_worker(conn, db):
    worker_id = input("输入要删除的工号: ").strip()
    if not worker_id:
        return
    db.remove(worker_id)
    conn.execute("DELETE FROM fingerprints WHERE worker_id=?", (worker_id,))
    conn.execute("DELETE FROM workers WHERE worker_id=?", (worker_id,))
    conn.commit()
    print("员工 {} 及其指纹已删除".format(worker_id))


# ============ 登录端 ============


def finger_login(conn, db, dev):
    if not db.users:
        print("尚未登记任何指纹, 请管理员先选 [1] 登记员工指纹 (先注册, 后使用)")
        return
    print("请按指纹 ({} 秒超时)...".format(LOGIN_TIMEOUT))
    try:
        frame = dev.acquire(poll_interval=0.15, timeout=LOGIN_TIMEOUT)
    except ZKFPMError as e:
        print("采集失败: {}".format(e))
        flash_led(dev, "red")  # 本次尝试失败红灯 (超时/设备故障)
        return
    try:
        worker_id, score = db.identify(frame.template)
    except MatchError:
        flash_led(dev, "red")  # 登录失败红灯
        print("未识别 (阈值 {:d}), 可用工号+密码登录".format(db.threshold_1_n))
        return
    flash_led(dev, "green")  # 登录成功绿灯
    print("识别: 工号 {}, 分数 {:d}".format(worker_id, score))
    do_login(worker_id)  # <- 自动登录


def password_login(conn):
    worker_id = input("工号: ").strip()
    if not worker_id:
        return
    password = input("密码: ")
    if check_password(worker_id, password):
        do_login(worker_id)
    else:
        print("密码错误")


# ============ 主循环 ============


def main():
    conn = open_store()
    db = load_db(conn)
    dev = None
    try:
        dev = Scanner()  # 设备缺失则只保留密码登录
        print("设备就绪: {} (已登记 {:d} 枚指纹, 用户 {})".format(dev.info().vid_pid, db.count, db.users))
    except ZKFPMError as e:
        print("!! 指纹设备不可用 ({}), 仅保留工号+密码登录".format(e))

    while True:
        print("\n===== 工业软件登录 =====")
        if dev is not None:
            print(" [1] 登记员工指纹")
            print(" [2] 指纹登录")
            print(" [3] 删除员工")
        print(" [p] 工号+密码登录 (兜底)")
        print(" [q] 退出")
        choice = input("选择: ").strip()
        try:
            if choice == "1" and dev is not None:
                register_worker(conn, db, dev)
            elif choice == "2" and dev is not None:
                finger_login(conn, db, dev)
            elif choice == "3" and dev is not None:
                remove_worker(conn, db)
            elif choice == "p":
                password_login(conn)
            elif choice == "q":
                break
        except ZKFPMError as e:
            print("操作失败: {}".format(e))
    if dev is not None:
        dev.close()
    conn.close()
    db.close()


if __name__ == "__main__":
    sys.exit(main())

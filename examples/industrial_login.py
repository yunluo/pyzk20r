# -*- coding: utf-8 -*-
"""工业现场登录改造参考实现: 指纹 1:N 识别自动登录 (工号 = user_id)。

场景: 制造业工业软件原"工号+密码"登录, 改造为登记指纹后刷指纹自动登录。

- 管理端 (班组长/管理员操作): 登记工号 + 指纹 (每人可多枚), 存 SQLite
- 登录端 (员工操作): 按指纹 -> identify 返回工号 -> 自动登录
- 灯效反馈由 :class:`FingerprintSession` 承担: 等待手指白灯常亮,
  登记/登录成功亮绿灯, 失败或超时亮红灯 (灯效参数 101/102/103 为只写,
  真机实测三色可用; 白灯同时是采像照明光源, 固件在采集时自动点亮)
- 兜底: 设备缺失/手指受伤时, 保留原工号+密码登录

本示例只负责**业务与存储** (SQLite 落盘 + 调你的工业软件登录 API);
采集/比对/灯效的编排交给库层的 FingerprintSession。

对接你自己的工业软件: 只需实现 ``do_login(工号)`` (弹窗/HTTP/MES API 皆可);
工号为任意字符串 (如 ``"A00123"``), 直接作为 user_id 使用。
"""

import sqlite3
import sys

from pyzk20r import (
    REASON_DEVICE_ERROR,
    REASON_NO_MATCH,
    REASON_NOT_ENROLLED,
    REASON_TIMEOUT,
    FingerprintDB,
    FingerprintSession,
    Scanner,
    ZKFPMError,
)

DB_FILE = "workers.db"

#: 等待手指的超时秒数 (无人值守防挂死, 到点按失败处理)
LOGIN_TIMEOUT = 60


# ============ 对接点: 换成你的工业软件登录调用 ============


def do_login(worker_id):
    # type: (str) -> None
    """识别成功后的自动登录动作, 把这里换成你的工业软件登录 API。"""
    print(">>> 自动登录: 工号 {} (此处调用你的工业软件)".format(worker_id))


def check_password(worker_id, password):
    # type: (str, str) -> bool
    """兜底登录的口令校验, 对接你原来的工号+密码逻辑。示例恒真。"""
    return True  # TODO: 接你原来的密码表/哈希校验


def on_session_event(event, data):
    # type: (str, dict) -> None
    """会话事件回调 (可选): 把状态提示打到控制台。

    事件名: wait_start / captured / success / failed / enroll_start /
    enroll_progress / enroll_done。
    """
    if event == "wait_start":
        print("   (白灯已亮, 请按手指)")
    elif event == "enroll_start":
        print("   (白灯已亮, 请依次按压手指 3 次)")


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


def register_worker(conn, db, session):
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
        result = session.wait_enroll(worker_id, timeout=LOGIN_TIMEOUT)
        if not result.ok:
            print("登记失败 ({}), 请重试".format(result.reason))
            break
        save_fingerprints(conn, worker_id, db.fingers_of(worker_id))
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


def finger_login(db, session):
    print("请按指纹 ({} 秒超时)...".format(LOGIN_TIMEOUT))
    result = session.wait_login(timeout=LOGIN_TIMEOUT)
    if result.ok:
        print("识别: 工号 {}, 分数 {:d}".format(result.user_id, result.score))
        do_login(result.user_id)  # <- 自动登录
        return
    if result.reason == REASON_NOT_ENROLLED:
        print("尚未登记任何指纹, 请管理员先选 [1] 登记员工指纹 (先注册, 后使用)")
    elif result.reason == REASON_NO_MATCH:
        print("未识别 (阈值 {:d}), 可用工号+密码登录".format(db.threshold_1_n))
    elif result.reason == REASON_TIMEOUT:
        print("等待手指超时, 可重试或用工号+密码登录")
    else:
        print("设备异常 ({}), 可改用工号+密码登录".format(REASON_DEVICE_ERROR))


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
    session = None
    try:
        dev = Scanner()  # 设备缺失则只保留密码登录
        print("设备就绪: {} (已登记 {:d} 枚指纹, 用户 {})".format(dev.info().vid_pid, db.count, db.users))
        session = FingerprintSession(db, dev, on_event=on_session_event)
    except ZKFPMError as e:
        print("!! 指纹设备不可用 ({}), 仅保留工号+密码登录".format(e))

    while True:
        print("\n===== 工业软件登录 =====")
        if session is not None:
            print(" [1] 登记员工指纹")
            print(" [2] 指纹登录")
            print(" [3] 删除员工")
        print(" [p] 工号+密码登录 (兜底)")
        print(" [q] 退出")
        choice = input("选择: ").strip()
        try:
            if choice == "1" and session is not None:
                register_worker(conn, db, session)
            elif choice == "2" and session is not None:
                finger_login(db, session)
            elif choice == "3" and session is not None:
                remove_worker(conn, db)
            elif choice == "p":
                password_login(conn)
            elif choice == "q":
                break
        except ZKFPMError as e:
            print("操作失败: {}".format(e))
    if session is not None:
        session.close()  # 熄灭全部灯 (不关闭设备/算法库, 所有权属本示例)
    if dev is not None:
        dev.close()
    conn.close()
    db.close()


if __name__ == "__main__":
    sys.exit(main())

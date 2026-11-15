# -*- coding: utf-8 -*-
"""指纹持久化示例: SQLite 存取 (库提供 dump/load 契约, 存储在应用层)。

首次运行: 登记 2 个用户并入库到 fingers.db;
再次运行: 从 fingers.db 恢复内存库, 直接进入识别循环 (无需重新按手指登记)。
"""

import os
import sqlite3

from pyzk20r import FingerprintDB, MatchError, Scanner

DB_FILE = "fingers.db"


def open_store():
    """打开 SQLite 存储 (应用层自行决定用什么介质: 文件/Redis/服务端均可)。"""
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS templates ("
        " user_id TEXT NOT NULL,"
        " tpl BLOB NOT NULL,"
        " seq INTEGER NOT NULL,"
        " PRIMARY KEY (user_id, seq))"
    )
    return conn


def load_db(conn):
    """从存储恢复内存指纹库。"""
    db = FingerprintDB()
    for user_id, tpl in conn.execute("SELECT user_id, tpl FROM templates ORDER BY user_id, seq"):
        db.add(user_id, tpl)
    return db


def save_user(conn, user_id, templates):
    """把一个用户的所有指纹写入存储 (覆盖式)。"""
    conn.execute("DELETE FROM templates WHERE user_id=?", (user_id,))
    conn.executemany(
        "INSERT INTO templates (user_id, tpl, seq) VALUES (?, ?, ?)",
        [(user_id, tpl, seq) for seq, tpl in enumerate(templates)],
    )
    conn.commit()


def main():
    first_run = not os.path.exists(DB_FILE)
    conn = open_store()

    if first_run or not conn.execute("SELECT 1 FROM templates").fetchone():
        db = FingerprintDB()
        with Scanner() as dev:
            for uid in ("1001", "1002"):
                print("工号 {}: 请按压手指 3 次...".format(uid))
                templates = dev.enroll()
                db.add(uid, db.merge(templates))
                save_user(conn, uid, db.fingers_of(uid))
                print("  已登记并写入 {}".format(DB_FILE))
    else:
        db = load_db(conn)
        print("从 {} 恢复 {:d} 枚指纹: 用户 {}".format(DB_FILE, db.count, db.users))

    print("进入识别循环, Ctrl+C 退出")
    try:
        with Scanner() as dev:
            for frame in dev.capture(poll_interval=0.1):
                try:
                    uid, score = db.identify(frame.template)
                    print(
                        "识别成功: 用户 {:d}, 分数 {:d} (阈值 {:d})".format(
                            uid, score, db.threshold_1_n
                        )
                    )
                except MatchError:
                    print("未识别")
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        conn.close()
        # 亦可用 dump()/load() 整库搬移:
        #   json.dump(db.dump(), open("fingers.json", "w"))
        #   db = FingerprintDB.load(json.load(open("fingers.json")))


if __name__ == "__main__":
    main()

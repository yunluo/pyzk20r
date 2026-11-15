# -*- coding: utf-8 -*-
"""FingerprintDB 测试: 一人多指纹、识别映射、阈值 (FakeDLL 注入, 标准库 unittest)。"""

import base64
import json
import unittest

from support import MERGED, TEMPLATE, FakeDLLTestCase

from pyzk20r import FingerprintDB, ZKFPMError
from pyzk20r.exceptions import ExtractError, LibraryError, MatchError, MergeError

TPL_A = bytes(range(1, 65))
TPL_B = bytes(range(65, 129))
TPL_C = bytes(range(129, 193))


class TestFingerprintDB(FakeDLLTestCase):
    """算法库层行为 (FakeDLL 注入)。"""

    def test_context_manager(self):
        with FingerprintDB() as db:
            self.assertEqual(db.count, 0)
        self.assertEqual(self.fake_dll.term_calls, 1)

    def test_add_and_count(self):
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            db.add("1002", TPL_B)
            self.assertEqual(db.count, 2)
            self.assertEqual(self.fake_dll.db_adds[1], TPL_A)
            self.assertEqual(self.fake_dll.db_adds[2], TPL_B)

    def test_one_user_multiple_fingerprints(self):
        """核心需求: 一个人登记多枚指纹。"""
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            db.add("1001", TPL_B)
            db.add("1001", TPL_C)
            db.add("1002", TPL_A)
            self.assertEqual(db.count, 4)
            self.assertEqual(db.users, ["1001", "1002"])
            self.assertEqual(db.fingers_of("1001"), [TPL_A, TPL_B, TPL_C])
            self.assertEqual(db.fingers_of("1002"), [TPL_A])

    def test_identify_maps_fid_to_user(self):
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            db.add("1001", TPL_B)
            db.add("2002", TPL_C)
            self.fake_dll.db_ident_result = (2, 95)  # fid=2 => "1001" 的第二枚
            user_id, score = db.identify(TPL_B)
            self.assertEqual(user_id, "1001")
            self.assertEqual(score, 95)
            self.fake_dll.db_ident_result = (3, 60)  # fid=3 => "2002"
            self.assertEqual(db.identify(TPL_C), ("2002", 60))

    def test_identify_no_match_raises(self):
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            self.fake_dll.db_ident_ret = -20
            with self.assertRaises(MatchError) as cm:
                db.identify(TPL_B)
            self.assertEqual(cm.exception.code, -20)

    def test_verify_best_of_user(self):
        """1:1 验证对该用户所有指纹取最优分。"""
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            db.add("1001", TPL_B)
            self.fake_dll.verify_scores = {1: 40, 2: 90}
            self.assertEqual(db.verify("1001", TPL_B), 90)

    def test_verify_unknown_user_raises(self):
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            with self.assertRaises(MatchError):
                db.verify("9999", TPL_A)

    def test_match(self):
        with FingerprintDB() as db:
            self.assertEqual(db.match(TPL_A, TPL_B), 88)
            self.fake_dll.match_score = -20
            with self.assertRaises(MatchError):
                db.match(TPL_A, TPL_B)

    def test_merge_requires_three(self):
        with FingerprintDB() as db:
            with self.assertRaises(MergeError):
                db.merge([TPL_A])
            self.assertEqual(db.merge([TPL_A, TPL_B, TPL_C]), MERGED)

    def test_merge_failure_raises(self):
        self.fake_dll.merge_ret = -22
        with FingerprintDB() as db:
            with self.assertRaises(MergeError):
                db.merge([TPL_A, TPL_B, TPL_C])

    def test_remove_user_removes_all_fingers(self):
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            db.add("1001", TPL_B)
            db.add("1002", TPL_C)
            db.remove("1001")
            self.assertEqual(db.users, ["1002"])
            self.assertEqual(db.count, 1)
            self.assertNotIn(1, self.fake_dll.db_adds)
            self.assertNotIn(2, self.fake_dll.db_adds)

    def test_remove_single_finger_by_template(self):
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            db.add("1001", TPL_B)
            db.remove("1001", TPL_A)
            self.assertEqual(db.fingers_of("1001"), [TPL_B])
            self.assertEqual(db.count, 1)

    def test_clear(self):
        with FingerprintDB() as db:
            db.add("1", TPL_A)
            db.clear()
            self.assertEqual(db.count, 0)
            self.assertEqual(db.users, [])

    def test_thresholds_default_and_set(self):
        """阈值默认值 50/70 (SDK 文档 5.2.23), 可读写。"""
        with FingerprintDB() as db:
            self.assertEqual(db.threshold_1_1, 50)
            self.assertEqual(db.threshold_1_n, 70)
            db.threshold_1_1 = 40
            db.threshold_1_n = 60
            self.assertEqual(db.threshold_1_1, 40)
            self.assertEqual(db.threshold_1_n, 60)
            self.assertEqual(self.fake_dll.db_params, {1: 40, 2: 60})

    def test_threshold_out_of_range_rejected(self):
        """阈值超出 0~1000 直接拒绝, 不写进 SDK。"""
        with FingerprintDB() as db:
            with self.assertRaises(ValueError):
                db.threshold_1_1 = -1
            with self.assertRaises(ValueError):
                db.threshold_1_n = 1001
            self.assertEqual(self.fake_dll.db_params, {1: 50, 2: 70})

    def test_extract_from_image(self):
        with FingerprintDB() as db:
            self.assertEqual(db.extract_from_image(r"C:\finger.bmp", dpi=500), TEMPLATE)

    def test_extract_from_image_load_fail(self):
        self.fake_dll.extract_ret = -26
        with FingerprintDB() as db:
            with self.assertRaises(ExtractError):
                db.extract_from_image(r"C:\missing.bmp")

    def test_dump_load_roundtrip(self):
        """dump -> load 往返: 一人多指纹关系与模板字节原样恢复。"""
        db = FingerprintDB()
        db.add("1001", TPL_A)
        db.add("1001", TPL_B)
        db.add("1002", TPL_C)
        data = db.dump()
        self.assertEqual(data["version"], 2)
        db.close()

        db2 = FingerprintDB.load(data)
        self.assertEqual(db2.users, ["1001", "1002"])
        self.assertEqual(db2.fingers_of("1001"), [TPL_A, TPL_B])
        self.assertEqual(db2.fingers_of("1002"), [TPL_C])
        self.assertEqual(db2.count, 3)
        db2.close()

    def test_dump_load_json_roundtrip(self):
        db = FingerprintDB()
        db.add("7", TPL_A)
        text = json.dumps(db.dump())  # 应用层用 json 落盘
        db.close()
        db2 = FingerprintDB.load(json.loads(text))
        self.assertEqual(db2.fingers_of("7"), [TPL_A])
        db2.close()

    def test_load_rejects_bad_payload(self):
        with self.assertRaises(ValueError):
            FingerprintDB.load({"version": 99, "templates": []})
        with self.assertRaises(ValueError):
            FingerprintDB.load({"version": 2})  # 缺 templates
        with self.assertRaises(ValueError):
            FingerprintDB.load({"version": 2, "templates": [["not-a-list"]]})
        with self.assertRaises(ValueError):
            FingerprintDB.load("not-a-dict")
        with self.assertRaises(ValueError):
            FingerprintDB.load({"version": 2, "templates": [[1, "!!not-base64!!"]]})

    def test_dump_empty_db(self):
        with FingerprintDB() as db:
            self.assertEqual(db.dump(), {"version": 2, "templates": []})

    def test_operations_on_closed_db_raise(self):
        db = FingerprintDB()
        db.close()
        with self.assertRaises(LibraryError):
            db.identify(TPL_A)
        with self.assertRaises(LibraryError):
            db.add("1", TPL_A)

    def test_identify_unmapped_fid_raises(self):
        """SDK 返回的 fid 不在映射内 (如被 clear 过) → MatchError 而非 KeyError。"""
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            self.fake_dll.db_ident_result = (99, 88)  # fid=99 从未入库
            with self.assertRaises(MatchError):
                db.identify(TPL_A)

    def test_identify_real_error_not_masked(self):
        """底层错误 (-17) 不应误报为"无匹配"(MatchError)。"""
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            self.fake_dll.db_ident_ret = -17
            with self.assertRaises(ZKFPMError) as cm:
                db.identify(TPL_A)
            self.assertNotIsInstance(cm.exception, MatchError)
            self.assertEqual(cm.exception.code, -17)

    def test_verify_surfaces_underlying_error(self):
        """所有指纹都返回同一底层错误 (-7) 时, 浮出该错误而非误报不匹配。"""
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            self.fake_dll.verify_scores = {1: -7}
            with self.assertRaises(ZKFPMError) as cm:
                db.verify("1001", TPL_A)
            self.assertNotIsInstance(cm.exception, MatchError)
            self.assertEqual(cm.exception.code, -7)

    def test_verify_mixed_error_and_match_returns_score(self):
        """一枚出错另一枚匹配成功 → 按成功的分数返回。"""
        with FingerprintDB() as db:
            db.add("1001", TPL_A)
            db.add("1001", TPL_B)
            self.fake_dll.verify_scores = {1: -7, 2: 60}
            self.assertEqual(db.verify("1001", TPL_A), 60)

    def test_numeric_worker_id_normalized(self):
        """数字工号: int 与 str 等价 (52193652 == "52193652"), identify 永远返回 str。"""
        with FingerprintDB() as db:
            db.add(52193652, TPL_A)  # int 形式登记
            self.assertEqual(db.users, ["52193652"])
            self.assertEqual(db.fingers_of(52193652), [TPL_A])  # int 查询命中
            self.assertEqual(db.fingers_of("52193652"), [TPL_A])  # str 查询命中同一工号
            self.fake_dll.db_ident_result = (1, 90)
            self.assertEqual(db.identify(TPL_A), ("52193652", 90))  # 识别返回归一化字符串
            self.fake_dll.verify_scores = {1: 88}
            self.assertEqual(db.verify(52193652, TPL_A), 88)  # int 形式 1:1 验证
            db.remove(52193652)
            self.assertEqual(db.users, [])

    def test_add_rejects_bool_and_blank(self):
        """bool 是 int 子类, 必须显式拒绝; 纯空白工号拒绝。"""
        with FingerprintDB() as db:
            with self.assertRaises(TypeError):
                db.add(True, TPL_A)
            with self.assertRaises(ValueError):
                db.add("   ", TPL_A)

    def test_add_rejects_bad_template(self):
        """模板入参防御: 类型/空/超长都在调用侧报错, 不透传给 SDK。"""
        with FingerprintDB() as db:
            with self.assertRaises(TypeError):
                db.add("1001", "not-bytes")
            with self.assertRaises(ValueError):
                db.add("1001", b"")
            with self.assertRaises(ValueError):
                db.add("1001", b"x" * 2049)
            # bytearray 合法, 归一化为 bytes
            db.add("1001", bytearray(TPL_A))
            self.assertEqual(db.fingers_of("1001"), [TPL_A])
            self.assertIsInstance(db.fingers_of("1001")[0], bytes)

    def test_match_rejects_bad_template(self):
        with FingerprintDB() as db:
            with self.assertRaises(TypeError):
                db.match("str", TPL_B)
            with self.assertRaises(ValueError):
                db.match(b"", TPL_B)
            with self.assertRaises(ValueError):
                db.identify(b"")

    def test_merge_rejects_bad_template(self):
        with FingerprintDB() as db:
            with self.assertRaises(ValueError):
                db.merge([TPL_A, b"", TPL_C])

    def test_load_rejects_invalid_base64_chars(self):
        """validate=True: 非 base64 字符不再被静默忽略 (损坏数据必须显式拒绝)。"""
        data = {"version": 2, "templates": [[1, "YWJj!"]]}
        with self.assertRaises(ValueError):
            FingerprintDB.load(data)

    def test_load_accepts_v1_int_ids(self):
        """v1 持久化结构 (整数工号) 仍可读取, 载入后归一化为字符串。"""
        data = {"version": 1, "templates": [[1001, base64.b64encode(TPL_A).decode("ascii")]]}
        db2 = FingerprintDB.load(json.loads(json.dumps(data)))
        self.assertEqual(db2.users, ["1001"])
        self.assertEqual(db2.fingers_of("1001"), [TPL_A])
        db2.close()


if __name__ == "__main__":
    unittest.main()

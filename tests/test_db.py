# -*- coding: utf-8 -*-
"""FingerprintDB 测试: 一人多指纹、识别映射、阈值 (FakeDLL 注入)。"""
import pytest
from conftest import MERGED, TEMPLATE

from pyzk20r import FingerprintDB
from pyzk20r.exceptions import ExtractError, LibraryError, MatchError, MergeError, ZKFPMError

TPL_A = bytes(range(1, 65))
TPL_B = bytes(range(65, 129))
TPL_C = bytes(range(129, 193))


def test_context_manager(fake_dll):
    with FingerprintDB() as db:
        assert db.count == 0
    assert fake_dll.term_calls == 1


def test_add_and_count(fake_dll):
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        db.add("1002", TPL_B)
        assert db.count == 2
        assert fake_dll.db_adds[1] == TPL_A
        assert fake_dll.db_adds[2] == TPL_B


def test_one_user_multiple_fingerprints(fake_dll):
    """核心需求: 一个人登记多枚指纹。"""
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        db.add("1001", TPL_B)
        db.add("1001", TPL_C)
        db.add("1002", TPL_A)
        assert db.count == 4
        assert db.users == ["1001", "1002"]
        assert db.fingers_of("1001") == [TPL_A, TPL_B, TPL_C]
        assert db.fingers_of("1002") == [TPL_A]


def test_identify_maps_fid_to_user(fake_dll):
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        db.add("1001", TPL_B)
        db.add("2002", TPL_C)
        fake_dll.db_ident_result = (2, 95)         # fid=2 => "1001" 的第二枚
        user_id, score = db.identify(TPL_B)
        assert user_id == "1001"
        assert score == 95
        fake_dll.db_ident_result = (3, 60)         # fid=3 => "2002"
        assert db.identify(TPL_C) == ("2002", 60)


def test_identify_no_match_raises(fake_dll):
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        fake_dll.db_ident_ret = -20
        with pytest.raises(MatchError) as ei:
            db.identify(TPL_B)
        assert ei.value.code == -20


def test_verify_best_of_user(fake_dll):
    """1:1 验证对该用户所有指纹取最优分。"""
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        db.add("1001", TPL_B)
        fake_dll.verify_scores = {1: 40, 2: 90}
        assert db.verify("1001", TPL_B) == 90


def test_verify_unknown_user_raises(fake_dll):
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        with pytest.raises(MatchError):
            db.verify("9999", TPL_A)


def test_match(fake_dll):
    with FingerprintDB() as db:
        assert db.match(TPL_A, TPL_B) == 88
        fake_dll.match_score = -20
        with pytest.raises(MatchError):
            db.match(TPL_A, TPL_B)


def test_merge_requires_three(fake_dll):
    with FingerprintDB() as db:
        with pytest.raises(MergeError):
            db.merge([TPL_A])
        assert db.merge([TPL_A, TPL_B, TPL_C]) == MERGED


def test_merge_failure_raises(fake_dll):
    fake_dll.merge_ret = -22
    with FingerprintDB() as db:
        with pytest.raises(MergeError):
            db.merge([TPL_A, TPL_B, TPL_C])


def test_remove_user_removes_all_fingers(fake_dll):
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        db.add("1001", TPL_B)
        db.add("1002", TPL_C)
        db.remove("1001")
        assert db.users == ["1002"]
        assert db.count == 1
        assert 1 not in fake_dll.db_adds
        assert 2 not in fake_dll.db_adds


def test_remove_single_finger_by_template(fake_dll):
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        db.add("1001", TPL_B)
        db.remove("1001", TPL_A)
        assert db.fingers_of("1001") == [TPL_B]
        assert db.count == 1


def test_clear(fake_dll):
    with FingerprintDB() as db:
        db.add("1", TPL_A)
        db.clear()
        assert db.count == 0
        assert db.users == []


def test_thresholds_default_and_set(fake_dll):
    with FingerprintDB() as db:
        assert db.threshold_1_1 == 35
        assert db.threshold_1_n == 55
        db.threshold_1_1 = 40
        db.threshold_1_n = 60
        assert db.threshold_1_1 == 40
        assert db.threshold_1_n == 60
        assert fake_dll.db_params == {1: 40, 2: 60}


def test_extract_from_image(fake_dll):
    with FingerprintDB() as db:
        assert db.extract_from_image(r"C:\finger.bmp", dpi=500) == TEMPLATE


def test_extract_from_image_load_fail(fake_dll):
    fake_dll.extract_ret = -26
    with FingerprintDB() as db:
        with pytest.raises(ExtractError):
            db.extract_from_image(r"C:\missing.bmp")


def test_dump_load_roundtrip(fake_dll):
    """dump -> load 往返: 一人多指纹关系与模板字节原样恢复。"""
    db = FingerprintDB()
    db.add("1001", TPL_A)
    db.add("1001", TPL_B)
    db.add("1002", TPL_C)
    data = db.dump()
    assert data["version"] == 2
    db.close()

    db2 = FingerprintDB.load(data)
    assert db2.users == ["1001", "1002"]
    assert db2.fingers_of("1001") == [TPL_A, TPL_B]
    assert db2.fingers_of("1002") == [TPL_C]
    assert db2.count == 3
    db2.close()


def test_dump_load_json_roundtrip(fake_dll):
    import json
    db = FingerprintDB()
    db.add("7", TPL_A)
    text = json.dumps(db.dump())                   # 应用层用 json 落盘
    db.close()
    db2 = FingerprintDB.load(json.loads(text))
    assert db2.fingers_of("7") == [TPL_A]
    db2.close()


def test_load_rejects_bad_payload(fake_dll):
    with pytest.raises(ValueError):
        FingerprintDB.load({"version": 99, "templates": []})
    with pytest.raises(ValueError):
        FingerprintDB.load({"version": 2})         # 缺 templates
    with pytest.raises(ValueError):
        FingerprintDB.load({"version": 2, "templates": [["not-a-list"]]})
    with pytest.raises(ValueError):
        FingerprintDB.load("not-a-dict")
    with pytest.raises(ValueError):
        FingerprintDB.load({"version": 2, "templates": [[1, "!!not-base64!!"]]})


def test_dump_empty_db(fake_dll):
    with FingerprintDB() as db:
        assert db.dump() == {"version": 2, "templates": []}


def test_operations_on_closed_db_raise(fake_dll):
    db = FingerprintDB()
    db.close()
    with pytest.raises(LibraryError):
        db.identify(TPL_A)
    with pytest.raises(LibraryError):
        db.add("1", TPL_A)


def test_identify_unmapped_fid_raises(fake_dll):
    """SDK 返回的 fid 不在映射内 (如被 clear 过) → MatchError 而非 KeyError。"""
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        fake_dll.db_ident_result = (99, 88)        # fid=99 从未入库
        with pytest.raises(MatchError):
            db.identify(TPL_A)


def test_identify_real_error_not_masked(fake_dll):
    """底层错误 (-17) 不应误报为"无匹配"(MatchError)。"""
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        fake_dll.db_ident_ret = -17
        with pytest.raises(ZKFPMError) as ei:
            db.identify(TPL_A)
        assert not isinstance(ei.value, MatchError)
        assert ei.value.code == -17


def test_verify_surfaces_underlying_error(fake_dll):
    """所有指纹都返回同一底层错误 (-7) 时, 浮出该错误而非误报不匹配。"""
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        fake_dll.verify_scores = {1: -7}
        with pytest.raises(ZKFPMError) as ei:
            db.verify("1001", TPL_A)
        assert not isinstance(ei.value, MatchError)
        assert ei.value.code == -7


def test_verify_mixed_error_and_match_returns_score(fake_dll):
    """一枚出错另一枚匹配成功 → 按成功的分数返回。"""
    with FingerprintDB() as db:
        db.add("1001", TPL_A)
        db.add("1001", TPL_B)
        fake_dll.verify_scores = {1: -7, 2: 60}
        assert db.verify("1001", TPL_A) == 60


def test_add_rejects_non_str_user_id(fake_dll):
    """user_id 必须为字符串 (工号), int 直接 TypeError。"""
    with FingerprintDB() as db:
        with pytest.raises(TypeError):
            db.add(1001, TPL_A)

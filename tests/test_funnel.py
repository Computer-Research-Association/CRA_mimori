"""
단계별 카운터 검증 (외부 의존 없음, 1초 미만).

실행:  uv run python tests/test_funnel.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quality_test.funnel import Funnel, load_funnel


def test_카운터가_누적된다():
    f = Funnel("before", "raw_sample.jsonl")
    f.inc_in("clean", 10)
    f.inc_out("clean", 8)
    f.drop("clean", "empty_content", 2)
    d = f.to_dict()
    assert d["stages"]["clean"]["in"] == 10, d
    assert d["stages"]["clean"]["out"] == 8, d
    assert d["stages"]["clean"]["dropped"]["empty_content"] == 2, d
    print("[OK] 카운터 누적")


def test_드롭_사유가_따로_집계된다():
    f = Funnel("r", "x.jsonl")
    f.drop("chunk", "too_short")
    f.drop("chunk", "too_short")
    f.drop("chunk", "no_keyword")
    dropped = f.to_dict()["stages"]["chunk"]["dropped"]
    assert dropped == {"too_short": 2, "no_keyword": 1}, dropped
    print("[OK] 사유별 집계")


def test_처음_보는_단계도_자동으로_생긴다():
    f = Funnel("r", "x.jsonl")
    f.inc_out("embed", 5)
    stage = f.to_dict()["stages"]["embed"]
    assert stage == {"in": 0, "out": 5, "dropped": {}}, stage
    print("[OK] 단계 자동 생성")


def test_run과_fixture_이름이_기록된다():
    d = Funnel("after", "sample.jsonl").to_dict()
    assert d["run"] == "after" and d["fixture"] == "sample.jsonl", d
    print("[OK] 메타 기록")


def test_저장하고_다시_읽으면_같다():
    f = Funnel("before", "raw.jsonl")
    f.inc_in("clean", 3)
    f.drop("clean", "empty_content")
    f.drop("clean", "본문없음")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "funnel.json")
        f.dump(path)
        assert load_funnel(path) == f.to_dict()
        # 사람이 읽을 수 있는 형태인지도 확인 (한글이 \uXXXX로 깨지지 않아야 함)
        with open(path, encoding="utf-8") as fp:
            raw = fp.read()
        assert "empty_content" in raw, raw
        # ensure_ascii=False 가 실제로 걸려 있는지 — True 로 되돌아가면 여기서 잡힌다
        assert "본문없음" in raw, raw
        assert "\\ubcf8" not in raw, raw
    print("[OK] 저장/로드 왕복")


def test_dump이_없는_디렉터리를_만든다():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nested", "deep", "funnel.json")
        Funnel("r", "x.jsonl").dump(path)
        assert os.path.exists(path)
    print("[OK] 상위 디렉터리 자동 생성")


if __name__ == "__main__":
    test_카운터가_누적된다()
    test_드롭_사유가_따로_집계된다()
    test_처음_보는_단계도_자동으로_생긴다()
    test_run과_fixture_이름이_기록된다()
    test_저장하고_다시_읽으면_같다()
    test_dump이_없는_디렉터리를_만든다()
    print("\nALL PASS ✅")

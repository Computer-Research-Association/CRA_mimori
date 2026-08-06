"""
preprocessing/pipeline.py의 재처리 트리거 안전장치(_build_reprocess_query) 검증.
Mongo 접촉 없음 — 쿼리 구성 + "전체 재처리 사고 방지" 가드만 순수 함수로 검증한다.

실행:  uv run python tests/test_reprocess_trigger.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from preprocessing.pipeline import _build_reprocess_query


def test_keyword만_지정하면_keyword_쿼리가_만들어진다():
    assert _build_reprocess_query(keyword="야르", source=None) == {"keyword": "야르"}
    print("[OK] keyword만 지정")


def test_source만_지정하면_source_쿼리가_만들어진다():
    assert _build_reprocess_query(keyword=None, source="tavily") == {"source": "tavily"}
    print("[OK] source만 지정")


def test_둘_다_지정하면_둘_다_쿼리에_들어간다():
    q = _build_reprocess_query(keyword="야르", source="tavily")
    assert q == {"keyword": "야르", "source": "tavily"}, q
    print("[OK] keyword+source 동시 지정")


def test_둘_다_없으면_에러_전체_재처리_사고_방지():
    try:
        _build_reprocess_query(keyword=None, source=None)
        assert False, "예외가 발생해야 함"
    except ValueError:
        print("[OK] keyword/source 둘 다 없으면 ValueError (전체 재처리 사고 방지)")


def test_all_sources가_True면_빈_쿼리로_전체_대상이_된다():
    # "실수로 둘 다 안 준 것"과 "명시적으로 전체를 원하는 것"을 구분한다.
    assert _build_reprocess_query(keyword=None, source=None, all_sources=True) == {}
    print("[OK] all_sources=True면 빈 쿼리(전체 대상), 에러 안 남")


def test_all_sources가_True여도_keyword를_같이_주면_keyword로_좁혀진다():
    # "전체 소스에서 이 키워드만"처럼 all_sources와 keyword는 같이 쓸 수 있다.
    assert _build_reprocess_query(keyword="야르", source=None, all_sources=True) == {"keyword": "야르"}
    print("[OK] all_sources=True + keyword 조합 시 keyword로 필터링")


if __name__ == "__main__":
    test_keyword만_지정하면_keyword_쿼리가_만들어진다()
    test_source만_지정하면_source_쿼리가_만들어진다()
    test_둘_다_지정하면_둘_다_쿼리에_들어간다()
    test_둘_다_없으면_에러_전체_재처리_사고_방지()
    test_all_sources가_True면_빈_쿼리로_전체_대상이_된다()
    test_all_sources가_True여도_keyword를_같이_주면_keyword로_좁혀진다()
    print("\nALL PASS ✅")

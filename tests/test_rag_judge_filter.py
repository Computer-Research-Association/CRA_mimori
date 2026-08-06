"""
analysis/rag_pipeline.py의 _is_valid_chunk()가 judge의 is_relevant를 실제로
검색 결과에서 걸러내는지 검증 (외부 의존 없음, Qdrant 서버 불필요).

judge(2단계)는 지금까지 is_relevant를 계산해 payload에 저장만 하고 아무 데도
안 썼다. 이번에 RAG 검색의 기존 청크 유효성 검증 단계(_is_valid_chunk)에
"is_relevant가 명시적으로 False면 제외" 조건을 추가해 실제로 작동시킨다.

반드시 False인 경우만 제외해야 한다 — judge 이전에 임베딩된 문서는 payload에
is_relevant 필드가 아예 없는데(None), 이런 문서까지 걸러지면 기존 데이터가
전부 갑자기 검색에서 사라지는 회귀가 생긴다.

실행:  uv run python tests/test_rag_judge_filter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client.http import models

from analysis.rag_pipeline import _is_valid_chunk


def _point(payload: dict) -> models.ScoredPoint:
    return models.ScoredPoint(id="p1", version=0, score=1.0, payload=payload)


def test_is_relevant가_False면_제외된다():
    p = _point({"text": "충분히 긴 관련없는 텍스트 내용입니다 야르", "title": "제목", "is_relevant": False})
    assert _is_valid_chunk(p, "야르", min_length=10) is False
    print("[OK] is_relevant=False 청크 제외")


def test_is_relevant가_True면_기존_검증을_통과한다():
    p = _point({"text": "충분히 긴 관련있는 텍스트 내용입니다 야르", "title": "제목", "is_relevant": True})
    assert _is_valid_chunk(p, "야르", min_length=10) is True
    print("[OK] is_relevant=True 청크는 그대로 통과")


def test_is_relevant_필드가_없으면_걸러지지_않는다():
    # judge 이전에 임베딩된 기존 데이터 — None과 False를 구분해야 회귀가 없다.
    p = _point({"text": "충분히 긴 관련있는 텍스트 내용입니다 야르", "title": "제목"})
    assert _is_valid_chunk(p, "야르", min_length=10) is True
    print("[OK] is_relevant 필드 없는 기존 데이터는 회귀 없이 통과")


def test_is_relevant가_True여도_키워드_자체_검증은_그대로_적용된다():
    # is_relevant 통과가 기존 키워드/길이 검증을 대체하지 않는다.
    p = _point({"text": "야르와 전혀 무관한 내용", "title": "제목", "is_relevant": True})
    assert _is_valid_chunk(p, "쌰갈", min_length=10) is False
    print("[OK] is_relevant=True라도 키워드 자체 불일치면 여전히 제외")


if __name__ == "__main__":
    test_is_relevant가_False면_제외된다()
    test_is_relevant가_True면_기존_검증을_통과한다()
    test_is_relevant_필드가_없으면_걸러지지_않는다()
    test_is_relevant가_True여도_키워드_자체_검증은_그대로_적용된다()
    print("\nALL PASS ✅")

"""
scripts/purge_irrelevant.py의 순수 판정 로직 검증 (외부 의존 없음, Mongo/Qdrant 불필요).

실행:  uv run python tests/test_purge_irrelevant.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.purge_irrelevant import _is_fully_irrelevant


def test_모든_청크가_False면_비관련_판정():
    doc = {"chunks": [{"is_relevant": False}, {"is_relevant": False}]}
    assert _is_fully_irrelevant(doc) is True
    print("[OK] 전부 False면 비관련 판정")


def test_하나라도_True면_비관련_아님():
    doc = {"chunks": [{"is_relevant": False}, {"is_relevant": True}]}
    assert _is_fully_irrelevant(doc) is False
    print("[OK] 하나라도 True면 삭제 대상 아님")


def test_is_relevant_필드_자체가_없으면_비관련_아님():
    # judge가 아직 안 돈 문서(EC2 배포 전 처리분) — 판정 대상이 아니라 안전하게 제외.
    doc = {"chunks": [{"text": "본문"}]}
    assert _is_fully_irrelevant(doc) is False
    print("[OK] is_relevant 필드 없으면(judge 미실행) 삭제 대상 아님")


def test_청크가_없으면_비관련_아님():
    doc = {"chunks": []}
    assert _is_fully_irrelevant(doc) is False
    print("[OK] 청크 없는 문서는 판정 대상 아님")


if __name__ == "__main__":
    test_모든_청크가_False면_비관련_판정()
    test_하나라도_True면_비관련_아님()
    test_is_relevant_필드_자체가_없으면_비관련_아님()
    test_청크가_없으면_비관련_아님()
    print("\nALL PASS ✅")

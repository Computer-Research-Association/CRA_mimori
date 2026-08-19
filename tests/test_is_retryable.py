"""
analysis.pipeline._is_retryable() 단위 테스트 (외부 의존 없음).

실행: uv run python tests/test_is_retryable.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.pipeline import _is_retryable


def test_504는_재시도_대상이_아니다():
    """실측(2026-08-19): 504는 서버 혼잡이 아니라 NVIDIA NIM 게이트웨이(NVCF) 자체의
    5분 타임아웃이다(9건 전부 elapsed≈302초로 동일 재현). 재시도해도 같은 이유로
    또 5분 걸려 또 실패할 뿐이라, 재시도 대상에서 뺀다."""
    assert _is_retryable("[504] Gateway Timeout\n{...}") is False
    print("[OK] 504 -> 재시도 안 함")


def test_다른_5xx는_여전히_재시도_대상이다():
    assert _is_retryable("[503] Service Unavailable") is True
    assert _is_retryable("[529] Overloaded") is True
    assert _is_retryable("[500] Internal Server Error") is True
    print("[OK] 503/529/500 등 다른 5xx는 그대로 재시도")


def test_코드_없이_키워드로만_판단되는_경우도_그대로다():
    assert _is_retryable("ResourceExhausted: quota exceeded") is True
    assert _is_retryable("Service Unavailable") is True
    assert _is_retryable("Overloaded, try again later") is True
    print("[OK] 키워드 기반 판단(ResourceExhausted 등)은 그대로 유지")


def test_재시도_불가능한_오류는_여전히_False다():
    assert _is_retryable("[401] Unauthorized") is False
    assert _is_retryable("invalid API key") is False
    print("[OK] 401 등 원래도 재시도 대상 아니던 것들은 그대로 False")


if __name__ == "__main__":
    test_504는_재시도_대상이_아니다()
    test_다른_5xx는_여전히_재시도_대상이다()
    test_코드_없이_키워드로만_판단되는_경우도_그대로다()
    test_재시도_불가능한_오류는_여전히_False다()
    print("\nALL PASS ✅")

"""
get_cached_trend 단위 테스트 (실제 Mongo 연결 없이 fake collection으로 검증).

실행: uv run python tests/test_trend_cache.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trend.trend_service import get_cached_trend


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        return self._docs[:n]


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query):
        matched = [d for d in self._docs if d.get("keyword") == query.get("keyword")]
        matched.sort(key=lambda d: d["date"], reverse=True)
        return _FakeCursor(matched)


def test_최신_날짜_문서를_반환한다():
    docs = [
        {"keyword": "야르", "date": "2026-08-05", "status": "평상"},
        {"keyword": "야르", "date": "2026-08-06", "status": "유행 중"},
        {"keyword": "다른키워드", "date": "2026-08-06", "status": "핫함"},
    ]
    result = get_cached_trend("야르", collection=_FakeCollection(docs))
    assert result is not None
    assert result["date"] == "2026-08-06", result
    assert result["status"] == "유행 중", result
    print("[OK] 최신 날짜 문서 반환")


def test_데이터_없으면_None():
    result = get_cached_trend("없는키워드", collection=_FakeCollection([]))
    assert result is None
    print("[OK] 데이터 없을 때 None")


if __name__ == "__main__":
    test_최신_날짜_문서를_반환한다()
    test_데이터_없으면_None()
    print("\nALL PASS ✅")

"""
get_cached_trend 단위 테스트 (실제 Mongo 연결 없이 fake collection으로 검증).

실행: uv run python tests/test_trend_cache.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trend.trend_service import get_cached_trend, get_latest_trend_for_keywords


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

    def aggregate(self, pipeline):
        """get_latest_trend_for_keywords가 쓰는 $match/$sort/$group 3단 파이프라인만 흉내낸다."""
        wanted = set(pipeline[0]["$match"]["keyword"]["$in"])
        matched = [d for d in self._docs if d.get("keyword") in wanted]
        matched.sort(key=lambda d: d["date"], reverse=True)
        latest_by_keyword = {}
        for doc in matched:
            latest_by_keyword.setdefault(doc["keyword"], doc)
        return [{"_id": kw, "doc": doc} for kw, doc in latest_by_keyword.items()]


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


def test_여러_키워드의_최신_문서를_한번에_가져온다():
    docs = [
        {"keyword": "야르", "date": "2026-08-05", "status": "평상"},
        {"keyword": "야르", "date": "2026-08-06", "status": "유행 중"},
        {"keyword": "쌰갈", "date": "2026-08-06", "status": "핫함"},
        {"keyword": "관심없는키워드", "date": "2026-08-06", "status": "핫함"},
    ]
    result = get_latest_trend_for_keywords(["야르", "쌰갈", "안크롤링된키워드"], collection=_FakeCollection(docs))
    assert set(result.keys()) == {"야르", "쌰갈"}, "요청한 목록에 없는 키워드가 섞이거나, 문서 없는 키워드가 키로 남으면 안 됨"
    assert result["야르"]["status"] == "유행 중", result["야르"]
    assert "_id" not in result["야르"], "집계 결과의 _id가 그대로 남아있음(jsonify 시 실패 원인)"
    print("[OK] 여러 키워드 최신 문서 일괄 조회 + _id 제거")


def test_빈_키워드_목록이면_빈_딕셔너리():
    result = get_latest_trend_for_keywords([], collection=_FakeCollection([]))
    assert result == {}
    print("[OK] 빈 목록 -> 빈 딕셔너리(aggregate 호출 안 함)")


if __name__ == "__main__":
    test_최신_날짜_문서를_반환한다()
    test_데이터_없으면_None()
    test_여러_키워드의_최신_문서를_한번에_가져온다()
    test_빈_키워드_목록이면_빈_딕셔너리()
    print("\nALL PASS ✅")

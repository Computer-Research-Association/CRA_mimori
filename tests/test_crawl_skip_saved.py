"""
crawlers/base.py의 filter_already_saved() 검증 (외부 의존 없음 — Mongo 대역 사용).

기존에는 본문 GET + 댓글 POST 를 모두 치른 뒤 insert_one 의 duplicate key 예외로
중복을 발견했다. 요청 하나가 도메인 RateLimiter 때문에 평균 2.3초를 물기 때문에,
이미 가진 글 하나마다 약 4.7초를 버리고 그 결과를 그대로 폐기했다.
이 함수는 그 요청들을 아예 보내지 않게 검색 목록 단계에서 걸러낸다.

실행:  uv run python tests/test_crawl_skip_saved.py
"""
import hashlib
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.base import filter_already_saved

_D1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_D2 = datetime(2026, 2, 1, tzinfo=timezone.utc)


def make_doc_id(keyword: str, url: str) -> str:
    """크롤러들이 쓰는 것과 동일한 규칙."""
    return hashlib.md5(f"{keyword}::{url}".encode()).hexdigest()


class FakeCollection:
    """find({"_id": {"$in": [...]}}, ...) 만 흉내내는 최소 대역."""

    def __init__(self, existing_ids):
        self.existing = set(existing_ids)
        self.calls = 0

    def find(self, query, projection=None):
        self.calls += 1
        wanted = query["_id"]["$in"]
        return [{"_id": i} for i in wanted if i in self.existing]


def test_이미_저장된_글은_걸러진다():
    posts = [("https://a.com/1", _D1), ("https://a.com/2", _D2)]
    col = FakeCollection([make_doc_id("밈", "https://a.com/1")])
    fresh, skipped = filter_already_saved(col, "밈", posts, make_doc_id)
    assert fresh == [("https://a.com/2", _D2)], fresh
    assert skipped == 1, skipped
    print("[OK] 이미 저장된 글은 fetch 대상에서 제외됨")


def test_전부_새_글이면_그대로_통과한다():
    posts = [("https://a.com/1", _D1), ("https://a.com/2", _D2)]
    col = FakeCollection([])
    fresh, skipped = filter_already_saved(col, "밈", posts, make_doc_id)
    assert fresh == posts, fresh
    assert skipped == 0, skipped
    print("[OK] 새 글만 있으면 전부 통과")


def test_전부_이미_있으면_요청_대상이_0이_된다():
    posts = [("https://a.com/1", _D1), ("https://a.com/2", _D2)]
    col = FakeCollection([make_doc_id("밈", u) for u, _ in posts])
    fresh, skipped = filter_already_saved(col, "밈", posts, make_doc_id)
    assert fresh == [], fresh
    assert skipped == 2, skipped
    print("[OK] 전부 중복이면 요청을 한 건도 보내지 않음 (스케줄러 반복 실행 케이스)")


def test_키워드가_다르면_같은_URL이라도_새_글이다():
    """_id 가 (keyword, url) 조합이라 키워드별로 문서가 따로 쌓인다."""
    posts = [("https://a.com/1", _D1)]
    col = FakeCollection([make_doc_id("다른키워드", "https://a.com/1")])
    fresh, skipped = filter_already_saved(col, "밈", posts, make_doc_id)
    assert fresh == posts, fresh
    assert skipped == 0, skipped
    print("[OK] 키워드가 다르면 같은 URL이어도 수집 대상")


def test_DB_왕복은_한_번뿐이다():
    """URL마다 find_one을 돌면 요청 대신 DB 왕복이 병목이 된다."""
    posts = [(f"https://a.com/{i}", _D1) for i in range(50)]
    col = FakeCollection([])
    filter_already_saved(col, "밈", posts, make_doc_id)
    assert col.calls == 1, col.calls
    print("[OK] 50개 URL에도 DB 조회는 1회")


def test_빈_목록은_조회하지_않는다():
    col = FakeCollection([])
    fresh, skipped = filter_already_saved(col, "밈", [], make_doc_id)
    assert fresh == [] and skipped == 0
    assert col.calls == 0, col.calls
    print("[OK] 빈 목록이면 DB를 건드리지 않음")


def test_순서가_보존된다():
    posts = [(f"https://a.com/{i}", _D1) for i in range(5)]
    col = FakeCollection([make_doc_id("밈", "https://a.com/2")])
    fresh, _ = filter_already_saved(col, "밈", posts, make_doc_id)
    assert [u for u, _ in fresh] == [
        "https://a.com/0", "https://a.com/1", "https://a.com/3", "https://a.com/4",
    ], fresh
    print("[OK] 남은 글의 원래 순서가 유지됨")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n모든 테스트 통과")

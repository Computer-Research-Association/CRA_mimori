"""
crawlers/base.py의 filter_recently_rejected() / record_reject() 검증 (Mongo 대역 사용).

filter_already_saved()가 막지 못하는 나머지 낭비를 막는 장치다. 본문이 짧거나
키워드와 무관해서 걸러진 글은 memes에 저장되지 않으므로 '이미 저장됨' 판정에 안 걸리고,
그래서 매 실행마다 다시 받아서 다시 버려졌다(럭키비키 재실행 실측: 13건 받아 0건 저장).

실행:  uv run python tests/test_crawl_reject_cache.py
"""
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.base import filter_recently_rejected, record_reject

_D1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TTL = 30


def make_doc_id(keyword: str, url: str) -> str:
    return hashlib.md5(f"{keyword}::{url}".encode()).hexdigest()


class FakeRejects:
    """find(필터, 프로젝션) 와 replace_one(upsert) 만 흉내내는 최소 대역."""

    def __init__(self, docs=None, fail=False):
        self.docs = list(docs or [])
        self.fail = fail
        self.calls = 0

    def find(self, query, projection=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("mongo down")
        wanted = set(query["_id"]["$in"])
        cutoff = query["rejected_at"]["$gte"]
        return [
            {"_id": d["_id"]}
            for d in self.docs
            if d["_id"] in wanted and d["rejected_at"] >= cutoff
        ]

    def replace_one(self, flt, doc, upsert=False):
        if self.fail:
            raise RuntimeError("mongo down")
        self.docs = [d for d in self.docs if d["_id"] != flt["_id"]]
        self.docs.append(doc)


def _rejected(keyword, url, days_ago):
    return {
        "_id": make_doc_id(keyword, url),
        "rejected_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
    }


def test_최근_제외된_글은_다시_받지_않는다():
    posts = [("https://a.com/1", _D1), ("https://a.com/2", _D1)]
    rejects = FakeRejects([_rejected("밈", "https://a.com/1", days_ago=1)])
    kept, skipped = filter_recently_rejected(rejects, "밈", posts, make_doc_id, _TTL)
    assert kept == [("https://a.com/2", _D1)], kept
    assert skipped == 1, skipped
    print("[OK] 최근 제외 이력이 있으면 요청하지 않음")


def test_TTL이_지난_이력은_무시하고_다시_받는다():
    """영구 스킵이면 MIN_CONTENT_LEN 같은 기준을 고쳐도 옛 판정이 굳어버린다."""
    posts = [("https://a.com/1", _D1)]
    rejects = FakeRejects([_rejected("밈", "https://a.com/1", days_ago=_TTL + 1)])
    kept, skipped = filter_recently_rejected(rejects, "밈", posts, make_doc_id, _TTL)
    assert kept == posts, kept
    assert skipped == 0, skipped
    print("[OK] TTL 경과 후에는 다시 받아 재평가함")


def test_이력이_없으면_전부_통과한다():
    posts = [("https://a.com/1", _D1), ("https://a.com/2", _D1)]
    rejects = FakeRejects([])
    kept, skipped = filter_recently_rejected(rejects, "밈", posts, make_doc_id, _TTL)
    assert kept == posts and skipped == 0
    print("[OK] 이력 없으면 전부 수집 대상")


def test_조회_실패해도_크롤은_계속된다():
    """거절 이력은 최적화 장치일 뿐이라, 실패 시 느려질지언정 결과는 같아야 한다."""
    posts = [("https://a.com/1", _D1)]
    rejects = FakeRejects(fail=True)
    kept, skipped = filter_recently_rejected(rejects, "밈", posts, make_doc_id, _TTL)
    assert kept == posts, kept
    assert skipped == 0, skipped
    print("[OK] 이력 조회 실패 시 전체 수집으로 폴백")


def test_기록_실패해도_예외가_새지_않는다():
    rejects = FakeRejects(fail=True)
    record_reject(rejects, "밈", "https://a.com/1", "dcinside", "too_short", make_doc_id)
    print("[OK] 이력 기록 실패는 삼켜짐(수집 결과에 영향 없음)")


def test_기록한_뒤에는_걸러진다():
    posts = [("https://a.com/1", _D1)]
    rejects = FakeRejects([])
    record_reject(rejects, "밈", "https://a.com/1", "dcinside", "too_short", make_doc_id)
    kept, skipped = filter_recently_rejected(rejects, "밈", posts, make_doc_id, _TTL)
    assert kept == [] and skipped == 1, (kept, skipped)
    print("[OK] 기록 → 다음 실행에서 요청 생략 (왕복 사이클 성립)")


def test_같은_글을_다시_기록해도_중복되지_않는다():
    rejects = FakeRejects([])
    for _ in range(3):
        record_reject(rejects, "밈", "https://a.com/1", "dcinside", "too_short", make_doc_id)
    assert len(rejects.docs) == 1, rejects.docs
    print("[OK] upsert 라 이력이 쌓이지 않음")


def test_키워드가_다르면_별개_이력이다():
    posts = [("https://a.com/1", _D1)]
    rejects = FakeRejects([_rejected("다른키워드", "https://a.com/1", days_ago=1)])
    kept, skipped = filter_recently_rejected(rejects, "밈", posts, make_doc_id, _TTL)
    assert kept == posts and skipped == 0
    print("[OK] 키워드별로 이력이 분리됨")


def test_빈_목록은_조회하지_않는다():
    rejects = FakeRejects([])
    kept, skipped = filter_recently_rejected(rejects, "밈", [], make_doc_id, _TTL)
    assert kept == [] and skipped == 0
    assert rejects.calls == 0, rejects.calls
    print("[OK] 빈 목록이면 DB를 건드리지 않음")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n모든 테스트 통과")

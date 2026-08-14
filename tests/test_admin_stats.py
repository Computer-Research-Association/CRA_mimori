"""
admin_stats.get_admin_stats() 단위 테스트. 실제 Mongo 없이 fake collection으로 검증.

실행: uv run python tests/test_admin_stats.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_stats import get_admin_stats


class _FakeCountableCollection:
    def __init__(self, docs=None):
        self._docs = docs or []

    def count_documents(self, _filter):
        return len(self._docs)

    def aggregate(self, pipeline):
        wanted = set(pipeline[0]["$match"]["keyword"]["$in"])
        counts = {}
        for doc in self._docs:
            if doc["keyword"] not in wanted:
                continue
            key = (doc["keyword"], doc.get("source"))
            counts[key] = counts.get(key, 0) + 1
        return [{"_id": {"keyword": kw, "source": src}, "count": n} for (kw, src), n in counts.items()]

    def find(self, filter_, _projection=None):
        return [d for d in self._docs if all(d.get(k) == v for k, v in filter_.items())]


# get_admin_stats가 컬렉션 7개를 전부 요구하므로(넘기지 않은 건 실제 Mongo로 채워서
# 연결을 시도해버림), 매 테스트마다 채워야 하는 최소 기본 세트.
def _base_collections(**overrides):
    base = {
        key: _FakeCountableCollection()
        for key in ["memes", "cleaned_memes", "trend_scores", "crawl_requests",
                    "hidden_keywords", "llm_requests", "crawl_rejects"]
    }
    base.update(overrides)
    return base


def test_컬렉션별_문서_수와_키워드_개수를_반환한다():
    memes = _FakeCountableCollection([
        {"keyword": "야르"}, {"keyword": "야르"}, {"keyword": "야르"},
        {"keyword": "쌰갈"},
    ])
    stats = get_admin_stats(
        load_keywords_fn=lambda: ["야르", "쌰갈"],
        collections=_base_collections(memes=memes),
    )
    assert stats["keyword_count"] == 2, stats
    assert stats["collection_counts"]["memes"] == 4, stats
    assert set(stats["collection_counts"].keys()) == {
        "memes", "cleaned_memes", "trend_scores", "crawl_requests",
        "hidden_keywords", "llm_requests", "crawl_rejects",
    }, stats["collection_counts"]
    print("[OK] 키워드 개수 + 컬렉션별 문서 수(7개 전부)")


def test_키워드별_문서_수는_많은_순으로_정렬되고_출처별로_나뉜다():
    memes = _FakeCountableCollection([
        {"keyword": "쌰갈", "source": "dcinside"},
        {"keyword": "야르", "source": "dcinside"}, {"keyword": "야르", "source": "dcinside"},
        {"keyword": "야르", "source": "youtube"},
    ])
    stats = get_admin_stats(
        load_keywords_fn=lambda: ["야르", "쌰갈"],
        collections=_base_collections(memes=memes),
    )
    assert stats["per_keyword_doc_counts"] == [
        {"keyword": "야르", "total": 3, "by_source": {"dcinside": 2, "youtube": 1}},
        {"keyword": "쌰갈", "total": 1, "by_source": {"dcinside": 1}},
    ], stats["per_keyword_doc_counts"]
    print("[OK] 키워드별 문서 수 내림차순 정렬 + 출처별 분해")


def test_문서가_없는_키워드는_0건_빈_출처로_나온다():
    memes = _FakeCountableCollection([{"keyword": "야르", "source": "dcinside"}])
    stats = get_admin_stats(
        load_keywords_fn=lambda: ["야르", "문서없는키워드"],
        collections=_base_collections(memes=memes),
    )
    row = next(r for r in stats["per_keyword_doc_counts"] if r["keyword"] == "문서없는키워드")
    assert row == {"keyword": "문서없는키워드", "total": 0, "by_source": {}}, row
    print("[OK] 집계에 없는 키워드도 0건 빈 출처로 채워짐")


def test_상한에_막힌_키워드_목록을_반환한다():
    crawl_requests = _FakeCountableCollection([
        {"_id": "막힌키워드", "promotion_skipped": "cap"},
        {"_id": "정상키워드"},
    ])
    stats = get_admin_stats(
        load_keywords_fn=lambda: [],
        collections=_base_collections(crawl_requests=crawl_requests),
    )
    assert stats["capped_keywords"] == ["막힌키워드"], stats["capped_keywords"]
    print("[OK] promotion_skipped=cap 키워드만 알림 목록에 포함")


def test_keyword_cap_설정값을_그대로_반환한다():
    from config.config_cilent import MAX_BATCH_KEYWORDS
    stats = get_admin_stats(load_keywords_fn=lambda: [], collections=_base_collections())
    assert stats["keyword_cap"] == MAX_BATCH_KEYWORDS, stats["keyword_cap"]
    print("[OK] keyword_cap이 설정값과 일치")


def _fake_disk_usage(total, used, free):
    from types import SimpleNamespace
    return lambda path: SimpleNamespace(total=total, used=used, free=free)


def test_디스크_사용량이_GB_단위와_퍼센트로_변환된다():
    gb = 1024 ** 3
    stats = get_admin_stats(
        load_keywords_fn=lambda: [],
        collections=_base_collections(),
        disk_usage_fn=_fake_disk_usage(total=100 * gb, used=25 * gb, free=75 * gb),
    )
    assert stats["disk_usage"] == {
        "total_gb": 100.0, "used_gb": 25.0, "free_gb": 75.0, "used_percent": 25.0,
    }, stats["disk_usage"]
    print("[OK] 디스크 사용량 GB/퍼센트 변환")


def test_디스크_조회가_실패하면_None으로_그_섹션만_빠진다():
    def _boom(_path):
        raise OSError("permission denied")

    stats = get_admin_stats(
        load_keywords_fn=lambda: [],
        collections=_base_collections(),
        disk_usage_fn=_boom,
    )
    assert stats["disk_usage"] is None, stats["disk_usage"]
    print("[OK] 디스크 조회 실패 -> disk_usage: None (나머지 통계는 정상)")


if __name__ == "__main__":
    test_컬렉션별_문서_수와_키워드_개수를_반환한다()
    test_키워드별_문서_수는_많은_순으로_정렬되고_출처별로_나뉜다()
    test_문서가_없는_키워드는_0건_빈_출처로_나온다()
    test_상한에_막힌_키워드_목록을_반환한다()
    test_keyword_cap_설정값을_그대로_반환한다()
    test_디스크_사용량이_GB_단위와_퍼센트로_변환된다()
    test_디스크_조회가_실패하면_None으로_그_섹션만_빠진다()
    print("\nALL PASS ✅")

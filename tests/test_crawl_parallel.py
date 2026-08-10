"""
크롤 병렬화 검증 (네트워크/DB 불필요, 몇 초).

두 가지만 확인한다:
  1. RateLimiter 가 스레드 수와 무관하게 도메인 요청 rate 를 유지하는가
     (세마포어와 달리 '요청 rate' 를 직접 제어 — 리뷰 P1 핵심)
  2. 한 소스 크롤이 예외를 던져도 나머지 소스/키워드 결과가 유실되지 않고,
     실패와 '0건'이 구분되는가 (리뷰 P1 관측성 + 예외 격리)

실행:  uv run python tests/test_crawl_parallel.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import base
import main


def test_rate_limiter_holds_rate_under_many_threads():
    gap = 0.05
    limiter = base.RateLimiter(gap, gap)  # 지터 없이 고정 간격으로 검증
    stamps: list[float] = []
    stamps_lock = threading.Lock()

    def worker():
        limiter.acquire()
        with stamps_lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    min_gap = min(gaps)
    # 12개 스레드가 동시에 달려들어도 요청 시작 간격이 gap 아래로 내려가면 안 됨
    assert min_gap >= gap * 0.9, f"rate 초과: min_gap={min_gap:.3f} < {gap}"
    print(f"[OK] RateLimiter: 12스레드에서도 최소 간격 {min_gap:.3f}s >= {gap}s 유지")


def test_crawl_all_isolates_failures():
    def good(keyword):
        return [1, 2, 3]  # 3건 수집한 척 (MIN_COMMUNITY_DOCS_FOR_TAVILY=3 이상이라
        # Tavily 보완 단계가 트리거되지 않아야 함 — 아래 _no_network가 그걸 검증)

    def boom(keyword):
        raise RuntimeError("quota exceeded")

    def _no_network(keyword):
        raise AssertionError(
            f"'{keyword}': 커뮤니티 수집이 기준 이상인데 Tavily/DuckDuckGo가 호출됨 "
            "(2단계 보완 로직이 트리거되면 안 되는 케이스)"
        )

    orig_community, orig_tavily, orig_ddg = (
        main.COMMUNITY_CRAWLERS, main.crawl, main.crawl_duckduckgo,
    )
    try:
        main.COMMUNITY_CRAWLERS = {"good": good, "bad": boom}
        main.crawl = _no_network
        main.crawl_duckduckgo = _no_network

        results = main.crawl_all(["k1", "k2", "k3"])
    finally:
        main.COMMUNITY_CRAWLERS, main.crawl, main.crawl_duckduckgo = (
            orig_community, orig_tavily, orig_ddg,
        )

    assert set(results) == {"k1", "k2", "k3"}, f"키워드 유실: {set(results)}"
    for kw in ("k1", "k2", "k3"):
        assert results[kw]["good"] == (3, "ok"), results[kw]["good"]
        count, status = results[kw]["bad"]
        assert count == 0 and status.startswith("실패"), (count, status)
        assert "tavily" not in results[kw], "커뮤니티 수집 충분한데 Tavily가 호출됨"
    print(f"[OK] 예외 격리: 한 소스가 죽어도 3키워드 전부 결과 보존, 실패/0건 구분")
    print(f"[OK] 커뮤니티 합계가 기준 이상이면 Tavily/DuckDuckGo 호출 안 됨(네트워크 0건)")


if __name__ == "__main__":
    test_rate_limiter_holds_rate_under_many_threads()
    test_crawl_all_isolates_failures()
    print("\nALL PASS ✅")

"""
main.py의 Tavily 보완 크롤 우선순위 로직 검증 (네트워크/DB 불필요, 몇 초).

배치 크롤 우선순위: 커뮤니티 크롤러(dcinside/namuwiki/youtube/natepann/todayhumor)를
먼저 돌리고, 합계가 MIN_COMMUNITY_DOCS_FOR_TAVILY 미만인 키워드만 Tavily로 보완한다.
Tavily가 예외로 실패하면 DuckDuckGo로 한 번 더 보완한다.

확인하는 것 4가지:
  1. 커뮤니티 수집이 기준 이상이면 Tavily는 호출되지 않는다
  2. 기준 미만이면 Tavily가 호출되고, 성공하면 결과에 반영된다
  3. Tavily가 정상적으로 0건을 반환(빈 리스트)한 건 '실패'가 아니므로 DuckDuckGo를
     트리거하지 않는다
  4. Tavily가 예외를 던지면 DuckDuckGo가 호출되고, 두 결과가 모두 남는다
     (DuckDuckGo 성공이 Tavily 실패 이력을 덮어쓰지 않음)

실행:  uv run python tests/test_crawl_tavily_fallback.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from config.config_cilent import MIN_COMMUNITY_DOCS_FOR_TAVILY


class _Patched:
    """main 모듈의 크롤러 참조를 테스트 동안만 바꾸고 항상 원복한다."""

    def __init__(self, **overrides):
        self.overrides = overrides
        self.originals = {}

    def __enter__(self):
        for name, value in self.overrides.items():
            self.originals[name] = getattr(main, name)
            setattr(main, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.originals.items():
            setattr(main, name, value)


def _few(keyword):
    return [1]  # MIN_COMMUNITY_DOCS_FOR_TAVILY(3) 미만


def _many(keyword):
    return [1, 2, 3, 4]  # MIN_COMMUNITY_DOCS_FOR_TAVILY(3) 이상


def _no_call(keyword):
    raise AssertionError(f"'{keyword}': 호출되면 안 되는 크롤러가 호출됨")


def test_커뮤니티_충분하면_Tavily_호출_안됨():
    with _Patched(
        COMMUNITY_CRAWLERS={"dcinside": _many},
        crawl=_no_call,
        crawl_duckduckgo=_no_call,
    ):
        results = main.crawl_all(["k1"])
    assert "tavily" not in results["k1"], results["k1"]
    assert "duckduckgo" not in results["k1"], results["k1"]
    print(f"[OK] 커뮤니티 {MIN_COMMUNITY_DOCS_FOR_TAVILY}건 이상이면 Tavily 호출 안 됨")


def test_커뮤니티_부족하면_Tavily_호출되고_성공하면_반영():
    def tavily_ok(keyword):
        return [1, 2]

    with _Patched(
        COMMUNITY_CRAWLERS={"dcinside": _few},
        crawl=tavily_ok,
        crawl_duckduckgo=_no_call,
    ):
        results = main.crawl_all(["k1"])
    assert results["k1"]["tavily"] == (2, "ok"), results["k1"]
    assert "duckduckgo" not in results["k1"], "Tavily가 성공했는데 DuckDuckGo가 호출됨"
    print("[OK] 커뮤니티 부족 → Tavily 호출, 성공 시 결과에 반영")


def test_Tavily가_빈결과여도_실패가_아니므로_DDG_트리거_안됨():
    def tavily_empty(keyword):
        return []  # 최근 재크롤 스킵 등 정상적인 빈 결과

    with _Patched(
        COMMUNITY_CRAWLERS={"dcinside": _few},
        crawl=tavily_empty,
        crawl_duckduckgo=_no_call,
    ):
        results = main.crawl_all(["k1"])
    assert results["k1"]["tavily"] == (0, "ok"), results["k1"]
    assert "duckduckgo" not in results["k1"], "정상적인 0건인데 DuckDuckGo가 호출됨"
    print("[OK] Tavily의 정상적인 0건은 실패가 아니므로 DuckDuckGo를 트리거하지 않음")


def test_Tavily_실패하면_DDG_호출되고_둘다_결과에_남음():
    def tavily_fails(keyword):
        raise RuntimeError("quota exceeded")

    def ddg_ok(keyword):
        return [1]

    with _Patched(
        COMMUNITY_CRAWLERS={"dcinside": _few},
        crawl=tavily_fails,
        crawl_duckduckgo=ddg_ok,
    ):
        results = main.crawl_all(["k1"])
    count, status = results["k1"]["tavily"]
    assert count == 0 and status.startswith("실패"), results["k1"]["tavily"]
    assert results["k1"]["duckduckgo"] == (1, "ok"), results["k1"]["duckduckgo"]
    print("[OK] Tavily 실패 → DuckDuckGo 호출, 두 결과 모두 보존(덮어쓰지 않음)")


def test_키워드별로_독립적으로_판단됨():
    """한 키워드가 기준 이상이어도 다른 키워드는 여전히 Tavily가 필요할 수 있다."""
    calls = []

    def community(keyword):
        return [1, 2, 3, 4] if keyword == "충분" else [1]

    def tavily_ok(keyword):
        calls.append(keyword)
        return [9]

    with _Patched(
        COMMUNITY_CRAWLERS={"dcinside": community},
        crawl=tavily_ok,
        crawl_duckduckgo=_no_call,
    ):
        results = main.crawl_all(["충분", "부족"])
    assert calls == ["부족"], calls
    assert "tavily" not in results["충분"]
    assert results["부족"]["tavily"] == (1, "ok")
    print("[OK] 키워드별 독립 판단 — 기준 이상인 키워드는 Tavily를 건너뜀")


if __name__ == "__main__":
    test_커뮤니티_충분하면_Tavily_호출_안됨()
    test_커뮤니티_부족하면_Tavily_호출되고_성공하면_반영()
    test_Tavily가_빈결과여도_실패가_아니므로_DDG_트리거_안됨()
    test_Tavily_실패하면_DDG_호출되고_둘다_결과에_남음()
    test_키워드별로_독립적으로_판단됨()
    print("\nALL PASS ✅")

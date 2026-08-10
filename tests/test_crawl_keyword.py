"""
main.py의 crawl_keyword()/add_keyword_if_missing() 검증 (네트워크/DB 불필요, 몇 초).
issue #69 (온디맨드 RAG 크롤)에서 rag_main.py가 재사용하는 두 함수.

확인하는 것:
  1. crawl_keyword: 커뮤니티 수집이 기준 이상이면 Tavily를 호출하지 않는다
  2. crawl_keyword: 기준 미만이면 Tavily로 보완하고, 실패하면 DuckDuckGo까지 보완한다
  3. add_keyword_if_missing: 이미 있는 키워드는 추가하지 않고 False, 파일 변경 없음
  4. add_keyword_if_missing: 없는 키워드는 한 줄 추가하고 True, 재호출해도 중복 안 됨

실행:  uv run python tests/test_crawl_keyword.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class _Patched:
    """main 모듈의 속성을 테스트 동안만 바꾸고 항상 원복한다."""

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


def _no_call(keyword):
    raise AssertionError(f"'{keyword}': 호출되면 안 되는 크롤러가 호출됨")


def test_crawl_keyword_커뮤니티_충분하면_Tavily_호출_안됨():
    def many(keyword):
        return [1, 2, 3, 4]

    with _Patched(
        COMMUNITY_CRAWLERS={"dcinside": many},
        crawl=_no_call,
        crawl_duckduckgo=_no_call,
    ):
        results = main.crawl_keyword("k1")
    assert results["dcinside"] == (4, "ok"), results
    assert "tavily" not in results, results
    print("[OK] crawl_keyword: 커뮤니티 충분하면 Tavily 호출 안 됨")


def test_crawl_keyword_커뮤니티_부족하면_Tavily_실패시_DDG_보완():
    def few(keyword):
        return [1]

    def tavily_fails(keyword):
        raise RuntimeError("quota exceeded")

    def ddg_ok(keyword):
        return [1, 2]

    with _Patched(
        COMMUNITY_CRAWLERS={"dcinside": few},
        crawl=tavily_fails,
        crawl_duckduckgo=ddg_ok,
    ):
        results = main.crawl_keyword("k1")
    count, status = results["tavily"]
    assert count == 0 and status.startswith("실패"), results["tavily"]
    assert results["duckduckgo"] == (2, "ok"), results["duckduckgo"]
    print("[OK] crawl_keyword: 커뮤니티 부족 + Tavily 실패 → DuckDuckGo 보완")


def test_add_keyword_if_missing_이미_있으면_False_추가안함():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("야르\n아자스\n")
        path = f.name
    try:
        with _Patched(KEYWORDS_PATH=path):
            added = main.add_keyword_if_missing("야르")
            with open(path, encoding="utf-8") as rf:
                content = rf.read()
        assert added is False
        assert content == "야르\n아자스\n"  # 변경 없음
        print("[OK] add_keyword_if_missing: 이미 있으면 False, 파일 변경 없음")
    finally:
        os.unlink(path)


def test_add_keyword_if_missing_없으면_추가하고_True():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("야르\n")
        path = f.name
    try:
        with _Patched(KEYWORDS_PATH=path):
            added = main.add_keyword_if_missing("신조어")
            with open(path, encoding="utf-8") as rf:
                content = rf.read()
        assert added is True
        assert content == "야르\n신조어\n"
        print("[OK] add_keyword_if_missing: 없으면 한 줄 추가하고 True")
    finally:
        os.unlink(path)


def test_add_keyword_if_missing_두번_호출해도_중복_안됨():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("야르\n")
        path = f.name
    try:
        with _Patched(KEYWORDS_PATH=path):
            first = main.add_keyword_if_missing("신조어")
            second = main.add_keyword_if_missing("신조어")
            with open(path, encoding="utf-8") as rf:
                content = rf.read()
        assert first is True and second is False
        assert content == "야르\n신조어\n"  # 한 번만 추가됨
        print("[OK] add_keyword_if_missing: 같은 키워드 두 번 호출해도 중복 추가 안 됨")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_crawl_keyword_커뮤니티_충분하면_Tavily_호출_안됨()
    test_crawl_keyword_커뮤니티_부족하면_Tavily_실패시_DDG_보완()
    test_add_keyword_if_missing_이미_있으면_False_추가안함()
    test_add_keyword_if_missing_없으면_추가하고_True()
    test_add_keyword_if_missing_두번_호출해도_중복_안됨()
    print("\nALL PASS ✅")

"""
tavily_crawler.py의 네이버 미러링 중복 제거 + 리다이렉트 URL 필터 검증 (외부 의존 없음).

실제 발견된 사례:
  - "https://m.blog.naver.com/jabsic/223438029933"가 검색 결과에 3번 중복 등장
  - "https://blog.naver.com/PostView.naver?blogId=mgraphic77&logNo=224339694888"와
    "https://blog.naver.com/mgraphic77/224339694888?viewType=pc"가 URL 형식만 다른 같은 글
  - "/goto?url=CAES..." 형식(스킴 없는 상대경로) = 실제 페이지가 아니라 구글 검색결과
    카드의 리다이렉트 추적 경로. content도 "...Read more"로 끝나는 스니펫일 뿐 원문이 아님.

실행:  uv run python tests/test_tavily_dedup.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.tavily_crawler import naver_blog_canonical_key, _is_fetchable_url, _merge_query_results


def test_모바일과_PC_네이버블로그는_같은_키다():
    a = naver_blog_canonical_key("https://m.blog.naver.com/jabsic/223438029933")
    b = naver_blog_canonical_key("https://blog.naver.com/jabsic/223438029933")
    assert a == b and a is not None, (a, b)
    print("[OK] m.blog.naver.com == blog.naver.com 같은 글 판정")


def test_PostView_쿼리형식과_경로형식은_같은_키다():
    a = naver_blog_canonical_key(
        "https://blog.naver.com/PostView.naver?blogId=mgraphic77&logNo=224339694888&redirect=Dlog"
    )
    b = naver_blog_canonical_key("https://blog.naver.com/mgraphic77/224339694888?viewType=pc")
    assert a == b and a is not None, (a, b)
    print("[OK] PostView.naver?blogId=..&logNo=.. == /blogId/logNo 같은 글 판정")


def test_다른_글은_다른_키다():
    a = naver_blog_canonical_key("https://blog.naver.com/jabsic/223438029933")
    b = naver_blog_canonical_key("https://blog.naver.com/influencer33/224041669522")
    assert a != b, (a, b)
    print("[OK] 다른 blogId/logNo는 다른 키")


def test_네이버_블로그가_아니면_None():
    assert naver_blog_canonical_key("https://m.kin.naver.com/qna/dirs/11080107/docs/476541504") is None
    assert naver_blog_canonical_key("https://example.com/post/1") is None
    print("[OK] 네이버 블로그가 아닌 URL은 None (지식인 등 다른 네이버 서비스 포함)")


def test_리다이렉트_URL은_페치_불가로_판정된다():
    url = "/goto?url=CAESzAQB7keqTT2BXI3rBHuZ0LaimUVgDPZmD_UkTwi0DjqCtRFMy-Jn7mPHFRHp9Ohrs4mUi86u9RG44UkKXoqLyP"
    assert _is_fetchable_url(url) is False
    print("[OK] 스킴 없는 리다이렉트 경로는 페치 불가 판정")


def test_정상_URL은_페치_가능으로_판정된다():
    assert _is_fetchable_url("https://blog.naver.com/abc/123") is True
    print("[OK] 정상 http(s) URL은 페치 가능 판정")


def test_여러_쿼리_결과가_URL_기준으로_합쳐진다():
    # 실측(2026-08-04): 현재 쿼리("+뜻 유래 밈 인터넷 커뮤니티")와 단독 키워드 쿼리는
    # 겹침이 8~17%뿐이라 조합해야 커버리지가 넓어진다. 같은 URL이 두 쿼리에 다
    # 나오면 먼저 나온 쿼리(현재 쿼리)의 결과를 유지한다.
    current = [{"url": "https://a.com/1", "title": "현재쿼리버전"}, {"url": "https://a.com/2", "title": "겹침"}]
    bare = [{"url": "https://a.com/2", "title": "단독쿼리버전(버려짐)"}, {"url": "https://a.com/3", "title": "단독전용"}]
    merged = _merge_query_results(current, bare)
    urls = [r["url"] for r in merged]
    assert urls == ["https://a.com/1", "https://a.com/2", "https://a.com/3"], urls
    assert merged[1]["title"] == "겹침", merged[1]
    print("[OK] 쿼리 결과 URL 기준 병합 (먼저 나온 쿼리 우선)")


def test_빈_쿼리_결과도_안전하게_처리된다():
    assert _merge_query_results([], []) == []
    print("[OK] 빈 결과 방어")


if __name__ == "__main__":
    test_모바일과_PC_네이버블로그는_같은_키다()
    test_PostView_쿼리형식과_경로형식은_같은_키다()
    test_다른_글은_다른_키다()
    test_네이버_블로그가_아니면_None()
    test_리다이렉트_URL은_페치_불가로_판정된다()
    test_정상_URL은_페치_가능으로_판정된다()
    test_여러_쿼리_결과가_URL_기준으로_합쳐진다()
    test_빈_쿼리_결과도_안전하게_처리된다()
    print("\nALL PASS ✅")

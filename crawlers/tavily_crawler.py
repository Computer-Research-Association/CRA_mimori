"""
tavily_crawler.py
키워드 입력 → Tavily 검색 → MongoDB 저장
"""

from tavily import TavilyClient
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs
import hashlib
import re
import sys
import os

from config.config_cilent import (
    TAVILY_API_KEY,
    TAVILY_MAX_RESULTS,
    TAVILY_MIN_SCORE,
    TAVILY_SEARCH_DEPTH,
    TAVILY_RECRAWL_DAYS,
)
from DB.mongo_client import get_collection

# blog.naver.com/{blogId}/{logNo} 형식(모바일 m.blog.naver.com도 동일 경로 구조).
_NAVER_BLOG_PATH_RE = re.compile(r"^/([\w\-]+)/(\d+)/?$")


def _is_fetchable_url(url: str) -> bool:
    """스킴(http/https)이 없는 상대경로는 실제 페이지가 아니라 검색결과 카드 자체일
    가능성이 높다. 예: tavily가 가끔 "/goto?url=CAES..." 같은 구글 리다이렉트
    추적 경로를 url로 돌려주는데, 이건 도메인도 없어 접속 불가능하고 content도
    "...Read more"로 끝나는 검색 스니펫이지 원문이 아니다."""
    return urlparse(url).scheme in ("http", "https")


def naver_blog_canonical_key(url: str) -> str | None:
    """네이버 블로그 URL을 (blogId, logNo) 기준 정규 키로 변환.

    같은 글이 모바일(m.blog.naver.com)/PC(blog.naver.com), 경로형식(/blogId/logNo)/
    쿼리형식(PostView.naver?blogId=..&logNo=..)으로 서로 다른 URL로 검색결과에
    중복 등장한다 — 실측: "m.blog.naver.com/jabsic/223438029933"가 한 검색에서
    3번 등장. make_doc_id()는 URL 문자열을 그대로 해시하므로 이 중복을 못 잡는다.
    같은 글이면 형식이 달라도 이 함수가 같은 키를 반환한다.
    네이버 블로그가 아니면(지식인 등 다른 네이버 서비스 포함) None을 반환한다.
    """
    parsed = urlparse(url)
    if "blog.naver.com" not in parsed.netloc:
        return None

    if parsed.path.rstrip("/").endswith("PostView.naver"):
        qs = parse_qs(parsed.query)
        blog_id = qs.get("blogId", [None])[0]
        log_no = qs.get("logNo", [None])[0]
        if blog_id and log_no:
            return f"naver_blog::{blog_id}::{log_no}"
        return None

    m = _NAVER_BLOG_PATH_RE.match(parsed.path)
    if m:
        return f"naver_blog::{m.group(1)}::{m.group(2)}"
    return None


def _merge_query_results(*result_lists: list[dict]) -> list[dict]:
    """여러 쿼리의 검색 결과를 URL 기준으로 합친다.

    실측(2026-08-04): 현재 쿼리("+뜻 유래 밈 인터넷 커뮤니티")와 키워드 단독 쿼리는
    겹침이 8~17%뿐이고 서로 상대가 못 찾는 고유한 소스를 갖고 있었다(현재 쿼리는
    언론 기사처럼 신조어를 직접 다루는 소스를, 단독 쿼리는 커뮤니티 질문/소셜
    게시물처럼 실제 용례를 반영한 소스를 더 잘 찾음). 그래서 하나만 쓰지 않고
    조합한다. 같은 URL이 여러 쿼리에 겹쳐 나오면 먼저 나온 쿼리의 결과를 유지한다.
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for results in result_lists:
        for r in results:
            url = r.get("url", "")
            if url in seen:
                continue
            seen.add(url)
            merged.append(r)
    return merged


def make_doc_id(keyword: str, url: str) -> str:
    """중복 방지용 ID — 키워드+URL 해시"""
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def build_document(keyword: str, result: dict) -> dict:
    """Tavily 결과 하나를 MongoDB 저장 스키마로 변환"""
    return {
        "_id": make_doc_id(keyword, result.get("url", "")),
        "keyword": keyword,
        "source": "tavily",
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "content": result.get("content", ""),      # 본문 (전처리 전 원본)
        "score": result.get("score", 0.0),         # Tavily 관련도 점수
        "published_date": result.get("published_date", None),
        "crawled_at": datetime.now(timezone.utc),
        "is_embedded": False,                       # 임베딩 완료 여부 (나중에 BGE-M3 연동 시 True로)
    }


def _already_crawled_recently(keyword: str, collection) -> bool:
    """TAVILY_RECRAWL_DAYS 이내에 이미 크롤된 키워드면 True — API 호출 생략용."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=TAVILY_RECRAWL_DAYS)
    return collection.find_one(
        {"keyword": keyword, "source": "tavily", "crawled_at": {"$gte": cutoff}},
        {"_id": 1},
    ) is not None


def crawl(keyword: str) -> list[dict]:
    """
    키워드로 Tavily 검색 후 MongoDB에 저장.
    반환: 저장된 문서 리스트
    """
    client = TavilyClient(api_key=TAVILY_API_KEY)
    collection = get_collection()

    if _already_crawled_recently(keyword, collection):
        print(f"[Tavily] '{keyword}' — {TAVILY_RECRAWL_DAYS}일 이내 크롤 이력 있음, 스킵")
        return []

    print(f"[Tavily] '{keyword}' 검색 시작...")

    # 두 갈래 쿼리를 조합한다 — 키워드를 따옴표로 감싸 동음이의어/부분 일치
    # 오염을 줄이는 건 공통. "+뜻 유래 밈 인터넷 커뮤니티" 수식어가 붙은 쿼리는
    # 신조어를 직접 다루는 언론 기사/설명형 콘텐츠를 잘 찾고, 키워드 단독 쿼리는
    # 커뮤니티 질문글·소셜 게시물처럼 실제 용례가 반영된 콘텐츠를 더 잘 찾는다.
    # 겹침이 8~17%뿐이라 하나만 쓰면 상대 쪽이 찾는 고유 소스를 놓친다.
    queries = [
        f'"{keyword}" 뜻 유래 밈 인터넷 커뮤니티',
        f'"{keyword}"',
    ]
    per_query_results = TAVILY_MAX_RESULTS // len(queries)

    query_result_lists = []
    for query in queries:
        response = client.search(
            query=query,
            search_depth=TAVILY_SEARCH_DEPTH,
            max_results=per_query_results,
            include_answer=False,       # 요약 답변 X, 원본 문서만
            include_raw_content=False,  # raw HTML 제외 (용량 절약)
        )
        query_result_lists.append(response.get("results", []))

    results = _merge_query_results(*query_result_lists)
    print(f"[Tavily] {len(results)}개 결과 수신 (쿼리 {len(queries)}개 조합, 중복 제거 후)")

    saved, skipped, low_score, not_fetchable, naver_dup = 0, 0, 0, 0, 0
    documents = []
    seen_naver_keys: set[str] = set()  # 이번 검색 응답 안에서의 네이버 블로그 중복만 잡는다

    for r in results:
        # relevance score 하한선 미달 결과는 오염 가능성이 높아 저장 제외
        if r.get("score", 0.0) < TAVILY_MIN_SCORE:
            low_score += 1
            continue

        url = r.get("url", "")
        if not _is_fetchable_url(url):
            not_fetchable += 1
            continue

        naver_key = naver_blog_canonical_key(url)
        if naver_key is not None:
            if naver_key in seen_naver_keys:
                naver_dup += 1
                continue
            seen_naver_keys.add(naver_key)

        doc = build_document(keyword, r)
        try:
            collection.insert_one(doc)
            saved += 1
            documents.append(doc)
        except Exception:
            # _id 중복 = 이미 존재하는 문서 → skip
            skipped += 1

    print(
        f"[MongoDB] 저장: {saved}개 / 스킵(중복): {skipped}개 "
        f"/ 저점수 제외(<{TAVILY_MIN_SCORE}): {low_score}개 "
        f"/ 리다이렉트 URL 제외: {not_fetchable}개 / 네이버 미러 중복 제외: {naver_dup}개"
    )
    return documents


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "아르"
    docs = crawl(keyword)

    print("\n--- 수집 결과 미리보기 ---")
    for d in docs[:3]:
        print(f"  제목: {d['title']}")
        print(f"  URL : {d['url']}")
        print(f"  점수: {d['score']:.3f}")
        print(f"  내용: {d['content'][:80]}...")
        print()

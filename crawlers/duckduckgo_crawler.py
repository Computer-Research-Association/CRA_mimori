"""
duckduckgo_crawler.py
Tavily 검색이 실패했을 때만 호출되는 폴백 검색 크롤러(main.py의 크롤 우선순위 로직 참고).

DuckDuckGo HTML 검색(html.duckduckgo.com/html/)을 파싱해 결과를 MongoDB에 저장한다.
API 키가 필요 없는 대신 검색 결과가 본문 전문이 아니라 스니펫(요약)만 준다 — Tavily가
이미 죽은 상황의 최후 보완 수단이라, 완전 공백보다는 스니펫이라도 있는 쪽이 낫다는
전제로 둔다. 그래서 결과 URL마다 원문 페이지를 따로 받아오지 않는다(요청을 검색 1건으로
끝내, 폴백 경로 자체가 또 실패할 여지를 늘리지 않는다).
"""

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, unquote, urlparse

from bs4 import BeautifulSoup

from config.config_cilent import DUCKDUCKGO_MAX_RESULTS, DUCKDUCKGO_RECRAWL_DAYS
from crawlers.base import make_session, safe_get
from DB.mongo_client import get_collection

SEARCH_URL = "https://html.duckduckgo.com/html/"
MIN_CONTENT_LEN = 30  # 스니펫이 이 미만이면 정보 없는 것으로 보고 제외


def make_doc_id(keyword: str, url: str) -> str:
    """중복 방지용 ID — 키워드+URL 해시. tavily_crawler.make_doc_id와 동일 규칙."""
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def _is_fetchable_url(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


def _resolve_target_url(href: str) -> str | None:
    """DDG html 결과 링크는 자체 클릭추적 리다이렉트(//duckduckgo.com/l/?uddg=<encoded>)를
    거친다. 그 리다이렉트를 실제로 따라가지 않고 uddg 파라미터에서 목적지 URL만 복원한다
    (요청 1건 추가를 피함)."""
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        return unquote(target) if target else None
    return href if _is_fetchable_url(href) else None


def _search(session, query: str) -> list[dict]:
    """DuckDuckGo HTML 검색 1페이지를 파싱해 (title, url, snippet) 목록을 반환."""
    url = f"{SEARCH_URL}?q={quote(query)}"
    resp = safe_get(session, url, referer="https://duckduckgo.com/")
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for div in soup.find_all("div", class_="result")[:DUCKDUCKGO_MAX_RESULTS]:
        title_a = div.find("a", class_="result__a")
        if not title_a:
            continue

        target_url = _resolve_target_url(title_a.get("href", ""))
        if not target_url:
            continue

        snippet_a = div.find("a", class_="result__snippet")
        results.append({
            "title": title_a.get_text(strip=True),
            "url": target_url,
            "snippet": snippet_a.get_text(strip=True) if snippet_a else "",
        })
    return results


def build_document(keyword: str, result: dict) -> dict:
    """DuckDuckGo 결과 하나를 MongoDB 저장 스키마로 변환. tavily_crawler.build_document와
    같은 필드 구성을 쓴다 — 전처리/임베딩 단계가 소스를 가리지 않고 동일하게 다루기 때문."""
    return {
        "_id": make_doc_id(keyword, result["url"]),
        "keyword": keyword,
        "source": "duckduckgo",
        "url": result["url"],
        "title": result.get("title", ""),
        "content": result.get("snippet", ""),  # 본문 전문이 아니라 검색 스니펫
        "score": 0.0,  # DDG html 검색은 관련도 점수를 주지 않음
        "published_date": None,
        "crawled_at": datetime.now(timezone.utc),
        "is_embedded": False,
    }


def _already_crawled_recently(keyword: str, collection) -> bool:
    """DUCKDUCKGO_RECRAWL_DAYS 이내에 이미 이 폴백으로 크롤된 키워드면 True.
    Tavily가 며칠째 계속 실패해도 이 폴백을 매일 다시 두드리지 않기 위함."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DUCKDUCKGO_RECRAWL_DAYS)
    return collection.find_one(
        {"keyword": keyword, "source": "duckduckgo", "crawled_at": {"$gte": cutoff}},
        {"_id": 1},
    ) is not None


def crawl_duckduckgo(keyword: str) -> list[dict]:
    """
    Tavily 실패 시의 폴백. 키워드로 DuckDuckGo 검색 후 MongoDB에 저장.
    반환: 저장된 문서 리스트
    """
    session = make_session()
    collection = get_collection()

    if _already_crawled_recently(keyword, collection):
        print(f"[DuckDuckGo] '{keyword}' — {DUCKDUCKGO_RECRAWL_DAYS}일 이내 폴백 이력 있음, 스킵")
        return []

    print(f"[DuckDuckGo] '{keyword}' 폴백 검색 시작...")
    results = _search(session, f'"{keyword}"')
    print(f"[DuckDuckGo] {len(results)}개 결과 수신")

    saved, skipped, too_short = 0, 0, 0
    documents = []

    for r in results:
        snippet = r.get("snippet", "").strip()
        if len(snippet) < MIN_CONTENT_LEN:
            too_short += 1
            continue

        doc = build_document(keyword, r)
        try:
            collection.insert_one(doc)
            saved += 1
            documents.append(doc)
        except Exception:
            # _id 중복 = 이미 존재하는 문서 → skip
            skipped += 1

    print(f"[MongoDB] 저장: {saved}개 / 스킵(중복): {skipped}개 / 스니펫 너무 짧음: {too_short}개")
    return documents


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "럭키비키"
    docs = crawl_duckduckgo(keyword)

    print("\n--- 수집 결과 미리보기 ---")
    for d in docs[:3]:
        print(f"  제목: {d['title']}")
        print(f"  URL : {d['url']}")
        print(f"  내용: {d['content'][:80]}...")
        print()

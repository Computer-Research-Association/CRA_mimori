"""
tavily_crawler.py
키워드 입력 → Tavily 검색 → MongoDB 저장
"""

from tavily import TavilyClient
from datetime import datetime, timezone
import hashlib
import sys
import os

from config.config_cilent import (
    TAVILY_API_KEY,
    TAVILY_MAX_RESULTS,
    TAVILY_MIN_SCORE,
    TAVILY_SEARCH_DEPTH,
)
from DB.mongo_client import get_collection


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


def crawl(keyword: str) -> list[dict]:
    """
    키워드로 Tavily 검색 후 MongoDB에 저장.
    반환: 저장된 문서 리스트
    """
    client = TavilyClient(api_key=TAVILY_API_KEY)
    collection = get_collection()

    print(f"[Tavily] '{keyword}' 검색 시작...")

    # 한국어 커뮤니티 맥락 강화를 위해 쿼리 보강.
    # 키워드를 따옴표로 감싸 동음이의어/부분 일치 오염을 줄임 (구문 일치 우선).
    query = f'"{keyword}" 뜻 유래 밈 인터넷 커뮤니티'

    response = client.search(
        query=query,
        search_depth=TAVILY_SEARCH_DEPTH,
        max_results=TAVILY_MAX_RESULTS,
        include_answer=False,       # 요약 답변 X, 원본 문서만
        include_raw_content=False,  # raw HTML 제외 (용량 절약)
    )

    results = response.get("results", [])
    print(f"[Tavily] {len(results)}개 결과 수신")

    saved, skipped, low_score = 0, 0, 0
    documents = []

    for r in results:
        # relevance score 하한선 미달 결과는 오염 가능성이 높아 저장 제외
        if r.get("score", 0.0) < TAVILY_MIN_SCORE:
            low_score += 1
            continue

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
        f"/ 저점수 제외(<{TAVILY_MIN_SCORE}): {low_score}개"
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

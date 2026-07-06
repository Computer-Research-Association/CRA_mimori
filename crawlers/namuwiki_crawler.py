"""
namuwiki_crawler.py
키워드 입력 → 나무위키 문서 수집 → MongoDB 저장

나무위키 특성:
- 댓글 없음. 대신 문서 본문 전체(섹션별)를 수집.
- 클래스명이 빌드마다 바뀌는 CSS Modules 구조이므로,
  안정적인 앵커 ID(a#s-1)를 기준으로 본문 컨테이너를 탐색함.
- 검색이 아닌 직접 URL 접근 방식: /w/{키워드}
"""

import hashlib
import re
import sys
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from config.config_cilent import CRAWL_MAX_POSTS
from crawlers.base import make_session, safe_get
from DB.mongo_client import get_collection


def make_doc_id(keyword: str, url: str) -> str:
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def _extract_article_body(soup: BeautifulSoup) -> str:
    """
    본문 컨테이너 추출 전략:
    1. 첫 번째 섹션 앵커(a#s-1)를 찾는다.
    2. 부모를 타고 올라가며 텍스트 길이가 처음으로 500자를 넘는 요소를 본문으로 판단.
    3. 거기서 [편집] 버튼 텍스트, 각주 번호 등 노이즈를 제거.

    왜 이 방식인가?
    - 나무위키는 CSS Modules를 사용해 클래스명이 배포마다 변경됨.
    - 클래스명 대신 섹션 앵커 ID(s-1, s-2 ...)는 항상 동일하므로 안정적.
    """
    anchor = soup.find("a", id="s-1")
    if not anchor:
        # 섹션이 없는 짧은 문서는 #app 전체 텍스트에서 추출
        app = soup.find(id="app")
        return app.get_text(separator="\n", strip=True) if app else ""

    # 부모를 타고 올라가며 본문 컨테이너 탐색
    node = anchor
    article_node = None
    while node.parent:
        node = node.parent
        if len(node.get_text(strip=True)) > 500:
            article_node = node
            break

    if not article_node:
        return ""

    # 노이즈 제거: [편집], [편집 요청] 버튼 텍스트
    for tag in article_node.find_all(string=re.compile(r"^\[편집")):
        tag.replace_with("")

    # 각주 영역 제거 (fn-N, rfn-N ID를 가진 span들의 상위 컨테이너)
    for tag in article_node.find_all("span", id=re.compile(r"^(fn|rfn)-")):
        parent = tag.parent
        if parent:
            parent.decompose()

    text = article_node.get_text(separator="\n", strip=True)

    # 연속 빈 줄 정리
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _get_section_titles(soup: BeautifulSoup) -> list[str]:
    """섹션 제목 목록 추출 (개요, 유래, 역사 등)"""
    spans = soup.find_all("span", id=re.compile(r"^[가-힣A-Za-z]"))
    return [s["id"] for s in spans]


def crawl_namuwiki(keyword: str) -> list[dict]:
    """
    키워드로 나무위키 문서 조회 → MongoDB 저장.

    나무위키는 검색이 아닌 직접 URL 접근(/w/{키워드})을 사용.
    문서가 없으면 404가 반환되므로 자동으로 건너뜀.
    """
    session = make_session()
    collection = get_collection()

    url = f"https://namu.wiki/w/{quote(keyword)}"
    print(f"[나무위키] '{keyword}' 문서 요청: {url}")

    resp = safe_get(session, url, referer="https://namu.wiki")
    if resp is None:
        print("[나무위키] 요청 실패 또는 차단")
        return []

    if resp.status_code == 404:
        print(f"[나무위키] '{keyword}' 문서 없음")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # 제목 추출
    title_tag = soup.find("title")
    title = title_tag.text.replace(" - 나무위키", "").strip() if title_tag else keyword

    # 본문 추출
    content = _extract_article_body(soup)
    if not content:
        print("[나무위키] 본문 추출 실패")
        return []

    sections = _get_section_titles(soup)
    print(f"[나무위키] 섹션: {sections}")
    print(f"[나무위키] 본문 길이: {len(content)}자")

    doc = {
        "_id": make_doc_id(keyword, url),
        "keyword": keyword,
        "source": "namuwiki",
        "url": url,
        "title": title,
        "content": content,
        "score": 0.0,
        "published_date": None,
        "crawled_at": datetime.now(timezone.utc),
        "is_embedded": False,
    }

    try:
        collection.insert_one(doc)
        print(f"[MongoDB] 저장 완료: {title}")
        return [doc]
    except Exception:
        print(f"[MongoDB] 스킵 (중복): {title}")
        return []


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "야르"
    docs = crawl_namuwiki(keyword)
    if docs:
        print(f"\n--- 미리보기 ---")
        print(f"제목: {docs[0]['title']}")
        print(f"내용:\n{docs[0]['content'][:300]}...")

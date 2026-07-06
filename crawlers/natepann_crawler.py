"""
natepann_crawler.py
키워드 검색 → 네이트판 게시글+댓글 수집 → MongoDB 저장

URL 구조:
  검색: https://pann.nate.com/search/talk?q={키워드}&page={n}
  게시글: https://pann.nate.com/talk/{숫자}

HTML 구조 (파악 기준):
  제목  : div.post-tit-info 의 첫 번째 텍스트 (작성자/날짜와 혼재)
  본문  : div.posting
  댓글  : div.cmt_list 안의 각 li 항목 텍스트
"""

import hashlib
import re
import sys
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from config.config_cilent import CRAWL_MAX_POSTS
from crawlers.base import make_session, safe_get
from DB.mongo_client import get_collection

BASE_URL = "https://pann.nate.com"


def make_doc_id(keyword: str, url: str) -> str:
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_post_urls(session, keyword: str, max_posts: int) -> list[str]:
    """
    검색 결과 페이지에서 게시글 URL 목록 수집.

    페이지네이션:
    - ?page=1, ?page=2, ... 파라미터로 다음 페이지 접근.
    - ul.s_list 안의 a[href=/talk/숫자] 링크가 실제 검색 결과.
    - 결과가 없는 페이지가 나오면 수집 중단 (중복 포함 가능성 있어 set으로 관리).
    """
    urls = []
    seen = set()
    page = 1

    while len(urls) < max_posts:
        search_url = f"{BASE_URL}/search/talk?q={quote(keyword)}&page={page}"
        resp = safe_get(session, search_url, referer=BASE_URL)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "lxml")
        result_ul = soup.find("ul", class_="s_list")
        if not result_ul:
            break

        links = result_ul.find_all("a", href=re.compile(r"/talk/\d+"))
        if not links:
            break  # 더 이상 결과 없음

        new_found = 0
        for a in links:
            href = a["href"]
            # 절대 URL로 변환 (/talk/123 → https://pann.nate.com/talk/123)
            full_url = urljoin(BASE_URL, href.split("#")[0])  # 댓글 앵커(#commentBox) 제거
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)
                new_found += 1
                if len(urls) >= max_posts:
                    break

        print(f"[네이트판] 페이지 {page}: {new_found}개 URL 수집 (누적: {len(urls)}개)")

        if new_found == 0:
            break  # 새 URL 없으면 중단

        page += 1

    return urls


def _parse_post(soup: BeautifulSoup) -> tuple[str, str]:
    """
    게시글 페이지에서 제목과 본문+댓글 텍스트를 추출.

    제목 추출 방법:
    - div.post-tit-info 안에 제목/작성자/날짜가 섞여 있음.
    - 첫 번째 텍스트 노드가 제목이므로, 자식 태그들의 텍스트를 제외하고 추출.

    댓글 추출 방법:
    - div.cmt_list 안에 각 댓글이 li 태그로 있음.
    - 닉네임/날짜/추천수 노이즈가 섞여 있으므로, 실제 댓글 텍스트 부분만 추출.
    """
    # 제목: post-tit-info의 첫 직접 텍스트 노드
    title = ""
    tit_div = soup.find(class_="post-tit-info")
    if tit_div:
        # 직접 자식 텍스트 노드만 추출 (하위 태그 텍스트 제외)
        for node in tit_div.children:
            text = node.get_text(strip=True) if hasattr(node, "get_text") else str(node).strip()
            if text and len(text) > 1:
                title = text
                break

    # 본문
    body = ""
    posting = soup.find(class_="posting")
    if posting:
        body = posting.get_text(separator="\n", strip=True)

    # 댓글
    comments = []
    cmt_list = soup.find(class_="cmt_list")
    if cmt_list:
        for li in cmt_list.find_all("li", recursive=False):
            # li 안의 실제 댓글 텍스트: 닉네임/날짜/버튼 제거 후 남은 텍스트
            # 노이즈 제거: 신고, 답글, 추천 버튼 텍스트
            for noise in li.find_all(class_=re.compile(r"btn|date|nick|reply_btn|report")):
                noise.decompose()
            cmt_text = li.get_text(separator=" ", strip=True)
            # 너무 짧거나 버튼 텍스트만 남은 경우 제외
            if len(cmt_text) > 3:
                comments.append(cmt_text)

    content = body
    if comments:
        content += "\n\n[댓글]\n" + "\n".join(comments)

    return title, content


def crawl_natepann(keyword: str) -> list[dict]:
    """
    키워드로 네이트판 검색 → 게시글+댓글 수집 → MongoDB 저장.
    """
    session = make_session()
    collection = get_collection()

    print(f"[네이트판] '{keyword}' 검색 시작...")
    post_urls = _get_post_urls(session, keyword, CRAWL_MAX_POSTS)
    print(f"[네이트판] 총 {len(post_urls)}개 URL 수집 완료")

    saved, skipped, failed = 0, 0, 0
    documents = []

    for url in post_urls:
        resp = safe_get(session, url, referer=f"{BASE_URL}/search/talk?q={quote(keyword)}")
        if resp is None:
            failed += 1
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        title, content = _parse_post(soup)

        if not content.strip():
            failed += 1
            continue

        # 제목이 추출 안 된 경우 URL에서 fallback
        if not title:
            title = f"네이트판 게시글 {url.split('/')[-1]}"

        doc = {
            "_id": make_doc_id(keyword, url),
            "keyword": keyword,
            "source": "natepann",
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
            saved += 1
            documents.append(doc)
        except Exception:
            skipped += 1

    print(f"[MongoDB] 저장: {saved}개 / 스킵(중복): {skipped}개 / 실패: {failed}개")
    return documents


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "야르"
    docs = crawl_natepann(keyword)
    if docs:
        print("\n--- 미리보기 ---")
        d = docs[0]
        print(f"제목: {d['title']}")
        print(f"내용 ({len(d['content'])}자):\n{d['content'][:400]}")

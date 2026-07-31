"""
natepann_crawler.py
키워드 검색 → 네이트판 게시글+댓글 수집 → MongoDB 저장

URL 구조:
  검색: https://pann.nate.com/search/talk?q={키워드}&sort={정렬}&page={n}
        (sort: PD 정확도 / DD 최신 / HD 인기 / VD 조회 / CD 댓글)
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

from config.config_cilent import (
    CRAWL_MAX_POSTS,
    CRAWL_MAX_SEARCH_PAGES,
    NATEPANN_SORT,
)
from crawlers.base import make_session, safe_get, get_date_cutoff
from DB.mongo_client import get_collection

BASE_URL = "https://pann.nate.com"
MIN_CONTENT_LEN = 30  # 본문+댓글 합산 최소 길이 (감탄사성 짧은 글 제외)


def make_doc_id(keyword: str, url: str) -> str:
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def _normalize(text: str) -> str:
    """키워드 매칭용 정규화 — 공백/특수문자 제거 + 소문자화."""
    return re.sub(r"[\s~!?.,'\"…]+", "", text).lower()


def _is_relevant(keyword: str, title: str, content: str) -> bool:
    """제목+본문에 키워드가 포함됐는지 확인 (정규화 후 부분 일치)."""
    return _normalize(keyword) in _normalize(f"{title} {content}")


def _parse_search_date(text: str) -> datetime | None:
    """
    검색 결과의 span.date 텍스트를 datetime으로 변환.
    형식: "08.04.18 14:00" (YY.MM.DD HH:MM) 또는 "08.04.18"
    오늘 글은 "14:00"처럼 시각만 표시 → 오늘 날짜로 처리.
    그 외 파싱 실패 시 None 반환 → 호출부에서 건너뜀.
    """
    text = text.strip()
    if re.match(r"^\d{1,2}:\d{2}$", text):
        return datetime.now(timezone.utc)
    for fmt in ("%y.%m.%d %H:%M", "%y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _get_post_urls(session, keyword: str, max_posts: int) -> list[tuple[str, datetime | None]]:
    """
    검색 결과 페이지에서 (게시글 URL, 작성일) 목록 수집.

    필터링 기준:
    - 정렬: NATEPANN_SORT (기본 HD=인기순) → 커뮤니티가 검증한 글 우선.
    - 날짜: get_date_cutoff()보다 오래된 글은 제외.
      인기순은 시간 순서가 아니므로 오래된 글이 나와도 중단하지 않고 건너뜀.

    페이지네이션:
    - ?page=1, ?page=2, ... 파라미터로 다음 페이지 접근.
    - ul.s_list 안의 li 항목마다 a[href=/talk/숫자] 링크 + span.date 날짜.
    - 날짜 필터로 인해 페이지를 무한정 넘길 수 있어 CRAWL_MAX_SEARCH_PAGES로 상한.
    """
    cutoff = get_date_cutoff()
    posts = []
    seen = set()
    page = 1

    while len(posts) < max_posts and page <= CRAWL_MAX_SEARCH_PAGES:
        search_url = f"{BASE_URL}/search/talk?q={quote(keyword)}&sort={NATEPANN_SORT}&page={page}"
        resp = safe_get(session, search_url, referer=BASE_URL)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "lxml")
        result_ul = soup.find("ul", class_="s_list")
        if not result_ul:
            break

        new_found = 0      # 이번 페이지에서 처음 본 URL 수 (루프 종료 판단용)
        old_skipped = 0    # 날짜 필터로 제외된 글 수
        parse_failed = 0   # 날짜 파싱 실패로 제외된 글 수

        for li in result_ul.find_all("li", recursive=False):
            a = li.find("a", href=re.compile(r"/talk/\d+"))
            if not a:
                continue
            # 절대 URL로 변환 (/talk/123 → https://pann.nate.com/talk/123)
            full_url = urljoin(BASE_URL, a["href"].split("#")[0])  # 댓글 앵커(#commentBox) 제거
            if full_url in seen:
                continue
            seen.add(full_url)
            new_found += 1

            date_span = li.find("span", class_="date")
            pub_date = _parse_search_date(date_span.get_text()) if date_span else None
            if pub_date is None:
                raw = date_span.get_text(strip=True) if date_span else "없음"
                print(f"[네이트판] 날짜 파싱 실패, 건너뜀: '{raw}'")
                parse_failed += 1
                continue
            if pub_date < cutoff:
                old_skipped += 1
                continue

            posts.append((full_url, pub_date))
            if len(posts) >= max_posts:
                break

        page_collected = new_found - old_skipped - parse_failed
        extra = f", {parse_failed}개 파싱실패" if parse_failed else ""
        print(f"[네이트판] 페이지 {page}: {page_collected}개 수집, {old_skipped}개 날짜 제외{extra} (누적: {len(posts)}개)")

        if new_found == 0:
            break  # 새 URL 없으면 중단

        page += 1

    return posts


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
    posts = _get_post_urls(session, keyword, CRAWL_MAX_POSTS)
    print(f"[네이트판] 총 {len(posts)}개 URL 수집 완료")

    saved, skipped, failed, irrelevant = 0, 0, 0, 0
    documents = []

    for url, pub_date in posts:
        resp = safe_get(session, url, referer=f"{BASE_URL}/search/talk?q={quote(keyword)}")
        if resp is None:
            failed += 1
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        title, content = _parse_post(soup)

        if not content.strip():
            failed += 1
            continue

        # 최소 길이: 감탄사성 짧은 글 제외
        if len(content.strip()) < MIN_CONTENT_LEN:
            print(f"[네이트판] 너무 짧음({len(content.strip())}자), 제외: {title[:40]!r}")
            irrelevant += 1
            continue

        # 관련성 게이트: 제목·본문에 키워드 없으면 제외
        if not _is_relevant(keyword, title, content):
            print(f"[네이트판] 관련 없음, 제외: {title[:40]!r}")
            irrelevant += 1
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
            "published_date": pub_date,
            "crawled_at": datetime.now(timezone.utc),
            "is_embedded": False,
        }

        try:
            collection.insert_one(doc)
            saved += 1
            documents.append(doc)
        except Exception:
            skipped += 1

    print(f"[MongoDB] 저장: {saved}개 / 스킵(중복): {skipped}개 / 실패: {failed}개 / 관련없음: {irrelevant}개")
    return documents


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "야르"
    docs = crawl_natepann(keyword)
    if docs:
        print("\n--- 미리보기 ---")
        d = docs[0]
        print(f"제목: {d['title']}")
        print(f"내용 ({len(d['content'])}자):\n{d['content'][:400]}")

"""
todayhumor_crawler.py
키워드 검색 → 오늘의유머 게시글 수집 → MongoDB 저장

URL 구조:
  검색: https://www.todayhumor.co.kr/board/list.php?kind=search&keyfield=subject&keyword={키워드}
        (제목만 검색 가능 — 본문 검색 옵션 없음. 여러 게시판(자유/유머자료/연예 등)을
        한 번에 검색해준다. 실측 확인: robots.txt가 일반 봇에 Allow: /, search=yes.)
  게시글: https://www.todayhumor.co.kr/board/view.php?table={게시판}&no={글번호}

댓글 — 이 크롤러는 댓글을 수집하지 않는다:
  게시글 페이지의 댓글 영역은 <div id='memoContainerDiv'></div>로 빈 채 내려오고
  JS/AJAX로 사후 로딩된다(실측 확인). 정적 스크래핑으로는 댓글을 가져올 수 없다.
  다른 4개 소스와 달리 이 크롤러가 만드는 문서엔 "[댓글]" 섹션이 항상 없다 —
  chunker/judge는 댓글 없는 문서를 이미 정상 처리하므로 파이프라인 쪽 변경은 불필요.

이미지 전용 게시물:
  '유머자료' 게시판 등은 이미지만 있고 텍스트 본문이 없는 경우가 흔하다
  (실측: "럭키비키" 검색 결과 11건 중 3건, 27%). content가 빈 문자열이면 저장하지 않는다.
"""

import hashlib
import re
import sys
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from config.config_cilent import CRAWL_MAX_POSTS
from crawlers.base import make_session, safe_get, get_date_cutoff
from DB.mongo_client import get_collection

BASE_URL = "https://www.todayhumor.co.kr"
MIN_CONTENT_LEN = 30  # 본문 최소 길이 (감탄사성 짧은 글 제외, 다른 크롤러와 동일 기준)


def make_doc_id(keyword: str, url: str) -> str:
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def _normalize(text: str) -> str:
    """키워드 매칭용 정규화 — 공백/특수문자 제거 + 소문자화 (dcinside/natepann과 동일 로직)."""
    return re.sub(r"[\s~!?.,'\"…]+", "", text).lower()


def _is_relevant(keyword: str, title: str, content: str) -> bool:
    """제목+본문에 키워드가 포함됐는지 확인 (정규화 후 부분 일치)."""
    return _normalize(keyword) in _normalize(f"{title} {content}")


def _parse_search_date(text: str) -> datetime | None:
    """
    검색 결과의 td.date 텍스트를 datetime으로 변환.
    형식: "26/02/24 08:21" (YY/MM/DD HH:MM). 파싱 실패 시 None 반환 → 호출부에서 건너뜀.
    """
    text = text.strip()
    try:
        return datetime.strptime(text, "%y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _get_post_urls(session, keyword: str, max_posts: int) -> list[tuple[str, datetime | None]]:
    """
    제목 검색 결과에서 (게시글 URL, 작성일) 목록을 수집.

    제목만 검색 가능하고(본문 검색 옵션 없음), 여러 게시판을 한 번에 검색해준다.
    날짜 컷오프(get_date_cutoff())보다 오래된 글은 제외한다.
    실측상 대부분의 키워드가 한 페이지 안에 다 들어와서, 다른 소스처럼 페이지네이션을
    강하게 활용하지 않는다 — 결과가 max_posts에 못 미쳐도 페이지 1개로 반환한다.
    """
    cutoff = get_date_cutoff()
    url = f"{BASE_URL}/board/list.php?kind=search&keyfield=subject&keyword={quote(keyword)}"
    resp = safe_get(session, url, referer=BASE_URL)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    seen: set[str] = set()
    posts: list[tuple[str, datetime | None]] = []

    for a in soup.find_all("a", href=lambda h: h and "view.php" in h and "no_tag" not in h):
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)

        row = a.find_parent("tr")
        date_td = row.find("td", class_="date") if row else None
        pub_date = _parse_search_date(date_td.get_text()) if date_td else None
        if pub_date is not None and pub_date < cutoff:
            continue

        full_url = BASE_URL + href if href.startswith("/") else href
        posts.append((full_url, pub_date))
        if len(posts) >= max_posts:
            break

    return posts


def _parse_post(soup: BeautifulSoup) -> tuple[str, str]:
    """
    게시글 페이지에서 제목과 본문 텍스트를 추출.

    댓글은 JS로 사후 로딩돼 정적 HTML에 없으므로 포함하지 않는다.
    이미지만 있는 게시물은 본문이 빈 문자열로 반환된다(호출부에서 스킵 처리).
    """
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
        title = re.sub(r"^오늘의유머\s*-\s*", "", title)

    body = ""
    content_div = soup.find(class_="viewContent")
    if content_div:
        body = content_div.get_text(separator="\n", strip=True)

    return title, body


def crawl_todayhumor(keyword: str) -> list[dict]:
    """
    키워드로 오늘의유머 검색 → 게시글 수집 → MongoDB 저장.
    """
    session = make_session()
    collection = get_collection()

    print(f"[오늘의유머] '{keyword}' 검색 시작...")
    posts = _get_post_urls(session, keyword, CRAWL_MAX_POSTS)
    print(f"[오늘의유머] {len(posts)}개 URL 수집 완료")

    saved, skipped, failed, irrelevant, no_content = 0, 0, 0, 0, 0
    documents = []

    for url, pub_date in posts:
        resp = safe_get(session, url, referer=f"{BASE_URL}/board/list.php?kind=search")
        if resp is None:
            failed += 1
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        title, content = _parse_post(soup)

        if not content.strip():
            no_content += 1
            continue

        if len(content.strip()) < MIN_CONTENT_LEN:
            print(f"[오늘의유머] 너무 짧음({len(content.strip())}자), 제외: {title[:40]!r}")
            irrelevant += 1
            continue

        if not _is_relevant(keyword, title, content):
            print(f"[오늘의유머] 관련 없음, 제외: {title[:40]!r}")
            irrelevant += 1
            continue

        doc = {
            "_id": make_doc_id(keyword, url),
            "keyword": keyword,
            "source": "todayhumor",
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
            # _id 중복 = 이미 존재하는 문서 → skip
            skipped += 1

    print(
        f"[MongoDB] 저장: {saved}개 / 스킵(중복): {skipped}개 / 실패: {failed}개 "
        f"/ 관련없음: {irrelevant}개 / 본문없음(이미지전용): {no_content}개"
    )
    return documents


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "야르"
    docs = crawl_todayhumor(keyword)

    if docs:
        print("\n--- 미리보기 ---")
        d = docs[0]
        print(f"제목: {d['title']}")
        print(f"URL : {d['url']}")
        print(f"내용 ({len(d['content'])}자):\n{d['content'][:400]}")

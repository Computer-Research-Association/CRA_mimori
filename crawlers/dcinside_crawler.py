"""
dcinside_crawler.py
키워드 검색 → 디시인사이드 게시글+댓글 수집 → MongoDB 저장

핵심 구조:
  검색  : https://search.dcinside.com/post/q/{키워드}/p/{페이지}
          → ul.sch_result_list 안의 a.tit_txt 링크가 게시글 URL
  게시글 : https://gall.dcinside.com/board/view/?id={갤ID}&no={글번호}
          → span.title_subject (제목), div.write_div (본문)
  댓글  : POST https://gall.dcinside.com/board/comment/
          → e_s_n_o 토큰을 게시글 HTML hidden input에서 매번 추출해야 함
          → 응답은 JSON {"comments": [{"memo": "댓글내용", ...}, ...]}

e_s_n_o란?
  DC Inside가 댓글 API 요청의 정당성을 검증하기 위해 사용하는 1회성 토큰.
  게시글 HTML을 렌더링할 때마다 새로 발급되므로, 댓글을 가져오려면
  반드시 게시글 페이지를 먼저 방문한 뒤 이 값을 추출해야 한다.
"""

import hashlib
import re
import sys
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from config.config_cilent import CRAWL_MAX_POSTS
from crawlers.base import make_session, safe_get, random_delay
from DB.mongo_client import get_collection

SEARCH_BASE = "https://search.dcinside.com"
GALL_BASE   = "https://gall.dcinside.com"


def make_doc_id(keyword: str, url: str) -> str:
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── 1. 검색 결과 URL 수집 ─────────────────────────────────────────────────────

def _get_post_urls(session, keyword: str, max_posts: int) -> list[str]:
    """
    통합 검색 페이지에서 게시글 URL 목록 수집.

    페이지네이션: /post/q/{키워드}/p/{n} 형식 (1부터 시작).
    각 li > a.tit_txt 가 게시글 링크.
    중복 제거를 위해 set으로 관리.
    """
    urls = []
    seen = set()
    page = 1

    while len(urls) < max_posts:
        search_url = f"{SEARCH_BASE}/post/q/{quote(keyword)}/p/{page}"
        resp = safe_get(session, search_url, referer=SEARCH_BASE)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "lxml")
        result_ul = soup.find("ul", class_="sch_result_list")
        if not result_ul:
            break

        links = result_ul.find_all("a", class_="tit_txt")
        if not links:
            break

        new_found = 0
        for a in links:
            href = a.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)
            urls.append(href)
            new_found += 1
            if len(urls) >= max_posts:
                break

        print(f"[디시인사이드] 페이지 {page}: {new_found}개 URL 수집 (누적: {len(urls)}개)")

        if new_found == 0:
            break
        page += 1

    return urls


# ── 2. 게시글 파싱 ────────────────────────────────────────────────────────────

def _parse_post(soup: BeautifulSoup) -> tuple[str, str, str, str, str]:
    """
    게시글 HTML에서 제목, 본문, 갤러리ID, 글번호, e_s_n_o 추출.

    e_s_n_o는 댓글 API 호출 시 필수 토큰으로,
    hidden input[name=e_s_n_o] 에서 가져온다.
    """
    def hidden(name: str) -> str:
        tag = soup.find("input", {"name": name})
        return tag["value"] if tag and tag.get("value") else ""

    # 제목
    title_tag = soup.find("span", class_="title_subject")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # 본문
    body_div = soup.find(class_="write_div")
    body = body_div.get_text(separator="\n", strip=True) if body_div else ""

    gallery_id = hidden("gallery_id") or hidden("id")
    post_no    = hidden("gallery_no") or hidden("no")
    e_s_n_o   = hidden("e_s_n_o")

    return title, body, gallery_id, post_no, e_s_n_o


# ── 3. 댓글 수집 ─────────────────────────────────────────────────────────────

def _fetch_comments(session, gallery_id: str, post_no: str, e_s_n_o: str,
                    gallery_type: str = "G") -> list[str]:
    """
    DC Inside 댓글 API(POST)를 호출해 댓글 텍스트 목록 반환.

    왜 POST인가?
    - DC Inside는 댓글을 별도 API로 분리하고, CSRF 방지를 위해
      e_s_n_o 토큰을 POST body에 포함시키도록 설계됨.
    - 토큰 없이 요청하면 '정상적인 접근이 아닙니다' 에러 반환.

    _GALLTYPE_ 파라미터:
    - 'G': 일반 갤러리 (gall.dcinside.com/board/...)
    - 'M': 마이너 갤러리 (gall.dcinside.com/mgallery/...)
    """
    random_delay()

    cmt_url = f"{GALL_BASE}/board/comment/"
    data = {
        "id":           gallery_id,
        "no":           post_no,
        "cmt_id":       gallery_id,
        "cmt_no":       post_no,
        "focus_cmt":    "-1",
        "e_s_n_o":      e_s_n_o,
        "comment_page": "1",
        "sort":         "D",
        "_GALLTYPE_":   gallery_type,
    }
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{GALL_BASE}/board/view/?id={gallery_id}&no={post_no}",
    }

    try:
        r = session.post(cmt_url, data=data, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        result = r.json()
        comments = result.get("comments") or []  # None 방어
        texts = []
        for c in comments:
            memo = c.get("memo", "") or ""
            if c.get("del_yn") != "N" or not memo.strip():
                continue
            # 댓글 안의 HTML 태그 제거 (디시콘 이미지, 답글 UI 등)
            clean = re.sub(r"<[^>]+>", "", memo).strip()
            if clean:
                texts.append(clean)
        return texts
    except Exception as e:
        print(f"[디시인사이드] 댓글 API 오류: {e}")
        return []


# ── 4. 메인 크롤러 ────────────────────────────────────────────────────────────

def crawl_dcinside(keyword: str) -> list[dict]:
    """
    키워드로 디시인사이드 검색 → 게시글+댓글 수집 → MongoDB 저장.
    """
    session = make_session()
    collection = get_collection()

    print(f"[디시인사이드] '{keyword}' 검색 시작...")
    post_urls = _get_post_urls(session, keyword, CRAWL_MAX_POSTS)
    print(f"[디시인사이드] 총 {len(post_urls)}개 URL 수집 완료")

    saved, skipped, failed = 0, 0, 0
    documents = []

    for url in post_urls:
        # 마이너 갤러리 여부 판별 (URL에 'mgallery'가 있으면 M 타입)
        gallery_type = "M" if "mgallery" in url else "G"

        resp = safe_get(session, url, referer=f"{SEARCH_BASE}/post/q/{quote(keyword)}")
        if resp is None:
            failed += 1
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        title, body, gallery_id, post_no, e_s_n_o = _parse_post(soup)

        if not body.strip():
            failed += 1
            continue

        # 댓글 수집 (e_s_n_o가 있을 때만)
        comments = []
        if e_s_n_o and gallery_id and post_no:
            comments = _fetch_comments(session, gallery_id, post_no, e_s_n_o, gallery_type)

        content = body
        if comments:
            content += "\n\n[댓글]\n" + "\n".join(comments)

        if not title:
            title = f"디시인사이드 {gallery_id} {post_no}"

        doc = {
            "_id":            make_doc_id(keyword, url),
            "keyword":        keyword,
            "source":         "dcinside",
            "url":            url,
            "title":          title,
            "content":        content,
            "score":          0.0,
            "published_date": None,
            "crawled_at":     datetime.now(timezone.utc),
            "is_embedded":    False,
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
    docs = crawl_dcinside(keyword)
    if docs:
        print("\n--- 미리보기 ---")
        d = docs[0]
        print(f"제목: {d['title']}")
        print(f"내용 ({len(d['content'])}자):\n{d['content'][:400]}")

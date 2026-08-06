"""
youtube_crawler.py
키워드 입력 → YouTube 검색 → 영상 제목/설명/댓글 수집 → MongoDB 저장

content 구성: 제목 + 설명 + [댓글] 섹션 (dcinside/natepann과 동일한 "[댓글]" 마커 사용).
댓글만 저장하면 밈의 뜻/유래 설명이 빠져 관련성 판정이 구조적으로 불가능하므로
영상 제목과 전체 설명을 본문 앞에 포함한다.
"""

import hashlib
import re
import sys
from datetime import datetime, timezone, timedelta

from googleapiclient.discovery import build

from config.config_cilent import (
    YOUTUBE_API_KEY,
    YOUTUBE_MAX_RESULTS,
    YOUTUBE_MAX_COMMENTS,
    YOUTUBE_ORDER,
    YOUTUBE_RECRAWL_DAYS,
)
from DB.mongo_client import get_collection


def make_doc_id(keyword: str, url: str) -> str:
    """중복 방지용 ID — 키워드+URL 해시 (Tavily crawler와 동일 방식)"""
    raw = f"{keyword}::{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def _build_youtube_client():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def search_videos(youtube, keyword: str, max_results: int = YOUTUBE_MAX_RESULTS):
    """
    키워드로 YouTube 영상 검색.
    - order: YOUTUBE_ORDER 설정값 (relevance / date / viewCount / rating)
    - 날짜 필터는 두지 않음: 밈이 유행하던 당시 영상이 가장 좋은 설명 소스이므로
      오래된 영상도 수집 대상. 품질은 제목/설명 키워드 필터(is_keyword_relevant)로 거름.
    """
    query = f"{keyword} 뜻 유래 밈"
    response = (
        youtube.search()
        .list(
            q=query,
            part="id,snippet",
            type="video",
            maxResults=max_results,
            relevanceLanguage="ko",
            order=YOUTUBE_ORDER,
        )
        .execute()
    )

    videos = []
    for item in response.get("items", []):
        videos.append(
            {
                "video_id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "published_at": item["snippet"].get("publishedAt"),
            }
        )
    return videos


def fetch_descriptions(youtube, video_ids: list[str]) -> dict[str, str]:
    """
    video_id → 전체 설명(description) 매핑.
    search().list의 snippet.description은 잘린 요약이라 본문으로 쓰기에 부족하므로
    videos().list로 전체 설명을 일괄 조회한다 (50개까지 한 번에 → 쿼터 절약).
    """
    descriptions: dict[str, str] = {}
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start:start + 50]
        try:
            response = (
                youtube.videos()
                .list(part="snippet", id=",".join(batch), maxResults=50)
                .execute()
            )
        except Exception as e:
            print(f"[YouTube] 설명 조회 실패 ({len(batch)}개): {e}")
            continue
        for item in response.get("items", []):
            descriptions[item["id"]] = item["snippet"].get("description", "")
    return descriptions


def _normalize(text: str) -> str:
    """키워드 매칭용 정규화 — 공백/물결/문장부호 제거 + 소문자화."""
    return re.sub(r"[\s~!?.,'\"…]+", "", text).lower()


def is_keyword_relevant(keyword: str, title: str, description: str) -> bool:
    """
    제목+설명에 키워드가 등장하는지 검사 (정규화 후 부분 일치).
    YouTube 검색이 느슨해 무관한 영상이 섞이므로 저장 전 싼 필터로 거른다.
    정밀 판정은 전처리 단계의 judge가 담당하므로 여기서는 완벽할 필요 없음.
    """
    return _normalize(keyword) in _normalize(f"{title} {description}")


def fetch_comments(youtube, video_id: str, max_comments: int = YOUTUBE_MAX_COMMENTS):
    """영상 ID로 댓글 가져오기 (관련도순, 답글 제외)"""
    comments = []
    try:
        response = (
            youtube.commentThreads()
            .list(
                part="snippet",
                videoId=video_id,
                maxResults=min(max_comments, 100),
                order="relevance",
                textFormat="plainText",
            )
            .execute()
        )
    except Exception as e:
        print(f"[YouTube] 댓글 수집 실패 (video_id={video_id}): {e}")
        return comments

    for item in response.get("items", [])[:max_comments]:
        snippet = item["snippet"]["topLevelComment"]["snippet"]
        comments.append(
            {
                "text": snippet.get("textDisplay", ""),
                "like_count": snippet.get("likeCount", 0),
            }
        )
    return comments


def build_document(keyword: str, video: dict, description: str, comments: list[dict]) -> dict:
    """
    영상 제목+설명+댓글을 MongoDB 저장 스키마로 변환 (Tavily와 동일 스키마).
    content는 "제목\n\n설명\n\n[댓글]\n..." 구조 — chunker의 "[댓글]" 구분자와 호환.
    """
    url = f"https://www.youtube.com/watch?v={video['video_id']}"
    content = f"{video['title']}\n\n{description}".strip()
    comment_text = "\n".join(c["text"] for c in comments if c["text"].strip())
    if comment_text:
        content += "\n\n[댓글]\n" + comment_text

    return {
        "_id": make_doc_id(keyword, url),
        "keyword": keyword,
        "source": "youtube",
        "url": url,
        "title": video["title"],
        "content": content,
        "score": 0.0,  # YouTube는 Tavily relevance score 없음 -> 0.0으로 통일
        "published_date": video.get("published_at"),
        "crawled_at": datetime.now(timezone.utc),
        "is_embedded": False,
    }


def _already_crawled_recently(keyword: str, collection) -> bool:
    """YOUTUBE_RECRAWL_DAYS 이내에 이미 크롤된 키워드면 True — API 호출 생략용."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=YOUTUBE_RECRAWL_DAYS)
    return collection.find_one(
        {"keyword": keyword, "source": "youtube", "crawled_at": {"$gte": cutoff}},
        {"_id": 1},
    ) is not None


def crawl_youtube(keyword: str) -> list[dict]:
    """
    키워드로 YouTube 영상 검색 -> 제목/설명 키워드 필터 -> 설명+댓글 수집 -> MongoDB 저장.
    댓글 수 하한 대신 키워드 등장 여부로 관련성을 거른다 — 설명이 좋은 영상은
    댓글이 적어도 가치가 있으므로.
    반환: 저장된 문서 리스트
    """
    youtube = _build_youtube_client()
    collection = get_collection()

    if _already_crawled_recently(keyword, collection):
        print(f"[YouTube] '{keyword}' — {YOUTUBE_RECRAWL_DAYS}일 이내 크롤 이력 있음, 스킵")
        return []

    print(f"[YouTube] '{keyword}' 검색 시작...")
    videos = search_videos(youtube, keyword)
    print(f"[YouTube] {len(videos)}개 영상 발견")

    descriptions = fetch_descriptions(youtube, [v["video_id"] for v in videos])

    saved, skipped, filtered, empty = 0, 0, 0, 0
    documents = []

    for video in videos:
        description = descriptions.get(video["video_id"], "")
        # 제목+설명에 키워드가 없으면 무관한 영상으로 보고 댓글 조회 전에 제외 (쿼터 절약)
        if not is_keyword_relevant(keyword, video["title"], description):
            filtered += 1
            continue

        comments = fetch_comments(youtube, video["video_id"])
        doc = build_document(keyword, video, description, comments)
        if not doc["content"].strip():
            empty += 1
            continue

        try:
            collection.insert_one(doc)
            saved += 1
            documents.append(doc)
        except Exception:
            # _id 중복 = 이미 존재하는 문서 → skip
            skipped += 1

    print(
        f"[MongoDB] 저장: {saved}개 / 스킵(중복): {skipped}개 "
        f"/ 키워드 불일치 제외: {filtered}개 / 본문 없음: {empty}개"
    )
    return documents


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "야르"
    docs = crawl_youtube(keyword)

    print("\n--- 수집 결과 미리보기 ---")
    for d in docs[:3]:
        print(f"  제목: {d['title']}")
        print(f"  URL : {d['url']}")
        print(f"  점수: {d['score']:.3f}")
        print(f"  내용: {d['content'][:80]}...")
        print()
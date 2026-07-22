"""
youtube_crawler.py
키워드 입력 → YouTube 검색 → 영상 댓글 수집 → MongoDB 저장
"""

import hashlib
import sys
from datetime import datetime, timezone

from googleapiclient.discovery import build

from config.config_cilent import (
    YOUTUBE_API_KEY,
    YOUTUBE_MAX_RESULTS,
    YOUTUBE_MAX_COMMENTS,
    YOUTUBE_MIN_COMMENTS,
    YOUTUBE_ORDER,
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
      오래된 영상도 수집 대상. 품질은 댓글 수 하한선(YOUTUBE_MIN_COMMENTS)으로 거름.
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


def build_document(keyword: str, video: dict, comments: list[dict]) -> dict:
    """영상+댓글 결과를 MongoDB 저장 스키마로 변환 (Tavily와 동일 스키마)"""
    url = f"https://www.youtube.com/watch?v={video['video_id']}"
    content = "\n".join(c["text"] for c in comments if c["text"].strip())

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


def crawl_youtube(keyword: str) -> list[dict]:
    """
    키워드로 YouTube 영상 검색 -> 댓글 수집 -> MongoDB에 저장.
    반환: 저장된 문서 리스트
    """
    youtube = _build_youtube_client()
    collection = get_collection()

    print(f"[YouTube] '{keyword}' 검색 시작...")
    videos = search_videos(youtube, keyword)
    print(f"[YouTube] {len(videos)}개 영상 발견")

    saved, skipped, empty = 0, 0, 0
    documents = []

    for video in videos:
        comments = fetch_comments(youtube, video["video_id"])
        # 댓글이 기준치 미만이면 키워드 관련 반응이 없는 영상으로 보고 제외
        if len(comments) < YOUTUBE_MIN_COMMENTS:
            empty += 1
            continue

        doc = build_document(keyword, video, comments)
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

    print(f"[MongoDB] 저장: {saved}개 / 스킵(중복): {skipped}개 / 댓글부족(<{YOUTUBE_MIN_COMMENTS}개): {empty}개")
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
"""공식 네이버 블로그 검색 API의 제목·요약을 수집한다. 본문 전문은 수집하지 않는다."""
import hashlib
import html
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pymongo.errors import DuplicateKeyError

from config.config_cilent import (
    NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, NAVER_BLOG_ENABLED, NAVER_BLOG_MAX_RESULTS,
)
from DB.mongo_client import get_collection
from quality_test.matching import find_keyword

SEARCH_URL = 'https://openapi.naver.com/v1/search/blog.json'


def build_document(keyword: str, item: dict) -> dict | None:
    """HTML 강조 태그를 제거하고 관련성이 있는 요약만 기존 저장 스키마로 변환한다."""
    title = html.unescape(BeautifulSoup(item.get('title', ''), 'html.parser').get_text())
    content = html.unescape(BeautifulSoup(item.get('description', ''), 'html.parser').get_text())
    url = item.get('link', '')
    if urlparse(url).scheme not in {'http', 'https'} or not urlparse(url).hostname:
        return None
    if len(content.strip()) < 30 or not find_keyword(keyword, title, content).matched:
        return None
    try:
        published = datetime.strptime(item.get('postdate', ''), '%Y%m%d').replace(
            tzinfo=timezone(timedelta(hours=9)))
    except ValueError:
        published = None
    return {
        '_id': hashlib.md5(f'{keyword}::{url}'.encode()).hexdigest(),
        'keyword': keyword, 'source': 'naver_blog', 'url': url, 'title': title,
        'content': content, 'content_kind': 'search_snippet', 'score': 0.0,
        'published_date': published, 'crawled_at': datetime.now(timezone.utc),
        'is_embedded': False,
    }


def crawl_naver_blog(keyword: str, max_posts: int = NAVER_BLOG_MAX_RESULTS) -> list[dict]:
    """검색 권한을 확인한 앱에서만 활성화한다. 장애는 상위 수집기의 소스 격리로 처리한다."""
    if not NAVER_BLOG_ENABLED:
        return []
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise ValueError('네이버 블로그 수집에 필요한 자격증명이 없습니다.')
    with requests.Session() as session:
        response = session.get(SEARCH_URL, params={
            'query': f'"{keyword}"', 'display': min(100, max(1, max_posts)), 'sort': 'date',
        }, headers={'X-Naver-Client-Id': NAVER_CLIENT_ID,
                    'X-Naver-Client-Secret': NAVER_CLIENT_SECRET}, timeout=(5, 15))
        response.raise_for_status()
        items = response.json()['items']
    collection = get_collection()
    documents = []
    for item in items:
        doc = build_document(keyword, item)
        if doc is None:
            continue
        try:
            collection.insert_one(doc)
        except DuplicateKeyError:
            continue
        documents.append(doc)
    print(f'[네이버 블로그] {keyword!r} — 검색 요약 {len(documents)}개 저장')
    return documents

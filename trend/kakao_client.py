"""
kakao_client.py
카카오 블로그/카페 검색 API 클라이언트.

각 문서의 datetime 필드를 날짜별로 버킷팅해
최근 N일 시계열 언급량을 한 번의 호출 세트로 즉시 확보한다.
cold-start 없음: 오늘 처음 키워드를 요청해도 30일치 시계열이 생긴다.

datalab_client 는 '검색 수요'(검색량 상대값)를 재는 반면
여기서는 '콘텐츠 공급'(블로그/카페 게시 건수)을 잰다. 성격이 다르므로
trend_service 에서 앙상블 가중치를 낮게 두어 보조 지표로만 쓴다.
"""

import os
from collections import defaultdict
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

_BLOG_URL = "https://dapi.kakao.com/v2/search/blog"
_CAFE_URL = "https://dapi.kakao.com/v2/search/cafe"
_PAGE_SIZE = 50
_MAX_PAGES = 10  # 소스당 최대 500건 수집


class KakaoSearchClient:
    """카카오 블로그/카페 언급량으로 키워드 시계열을 구성한다."""

    def __init__(self, rest_api_key: str = None):
        self.rest_api_key = rest_api_key or os.getenv("KAKAO_REST_API_KEY", "")
        if not self.rest_api_key:
            raise RuntimeError(
                "KAKAO_REST_API_KEY 가 설정되지 않았습니다. .env 를 확인하세요."
            )

    def _headers(self) -> dict:
        return {"Authorization": f"KakaoAK {self.rest_api_key}"}

    def _fetch_dates(self, url: str, keyword: str, cutoff: date) -> list[str]:
        """
        cutoff(포함) 이후 문서의 날짜(YYYY-MM-DD) 목록을 반환한다.

        recency 정렬이므로 cutoff 이전 문서가 처음 나오면 즉시 중단한다.
        datetime 은 ISO8601(±TZ 오프셋 포함)이라 앞 10자(YYYY-MM-DD)만
        슬라이싱해 date 로 파싱하면 파이썬 버전/타임존 처리와 무관하게 안전하다.
        """
        dates: list[str] = []
        for page in range(1, _MAX_PAGES + 1):
            resp = requests.get(
                url,
                headers=self._headers(),
                params={
                    "query": keyword,
                    "sort": "recency",
                    "page": page,
                    "size": _PAGE_SIZE,
                },
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            documents = payload.get("documents", [])
            if not documents:
                break

            for doc in documents:
                dt_str = doc.get("datetime", "")
                if not dt_str:
                    continue
                doc_date = date.fromisoformat(dt_str[:10])
                if doc_date < cutoff:
                    return dates  # recency 정렬이므로 이후는 전부 더 오래됨
                dates.append(doc_date.isoformat())

            if payload.get("meta", {}).get("is_end", True):
                break

        return dates

    def _daily_series(self, url: str, keyword: str, days: int) -> list[dict]:
        """
        한 채널(블로그 or 카페)의 일별 언급 건수 시계열을 만든다.

        반환 형식: [{"date": "YYYY-MM-DD", "ratio": float}, ...]  (날짜 오름차순)
        필드명을 ratio 로 맞춰 zscore 파이프라인과 인터페이스를 통일한다.
        언급이 없는 날은 0.0 으로 채워 baseline 이 끊기지 않게 한다.
        """
        today = date.today()
        cutoff = today - timedelta(days=days - 1)
        bucket: dict[str, int] = defaultdict(int)
        for d in self._fetch_dates(url, keyword, cutoff):
            bucket[d] += 1

        series = []
        for i in range(days - 1, -1, -1):
            day = (today - timedelta(days=i)).isoformat()
            series.append({"date": day, "ratio": float(bucket.get(day, 0))})
        return series

    def get_channel_counts(self, keyword: str, days: int = 30) -> dict:
        """
        블로그 / 카페 채널별 일별 시계열을 각각 반환한다.

        반환 형식: {"blog": [{"date","ratio"}...], "cafe": [{"date","ratio"}...]}
        채널을 분리 저장하면 'blog 중심 확산' vs '커뮤니티(cafe) 중심 유행'을
        구분해 해석·시각화할 수 있다. 채널당 API 호출은 1회씩만 발생한다.
        """
        return {
            "blog": self._daily_series(_BLOG_URL, keyword, days),
            "cafe": self._daily_series(_CAFE_URL, keyword, days),
        }

    def get_daily_counts(self, keyword: str, days: int = 30) -> list[dict]:
        """블로그 + 카페 합산 시계열(기존 호환). 채널별은 get_channel_counts 사용."""
        channels = self.get_channel_counts(keyword, days)
        return merge_daily_series(channels["blog"], channels["cafe"])


def merge_daily_series(*series_list: list[dict]) -> list[dict]:
    """
    같은 날짜 범위의 여러 일별 시계열을 날짜별 ratio 합산으로 병합한다.
    baseline 게이트를 '합산 기준'으로 걸 때 사용한다.
    """
    bucket: dict[str, float] = defaultdict(float)
    for series in series_list:
        for row in series:
            bucket[row["date"]] += float(row["ratio"])
    return [
        {"date": day, "ratio": bucket[day]}
        for day in sorted(bucket)
    ]


if __name__ == "__main__":
    kw = input("키워드 입력: ").strip()
    client = KakaoSearchClient()
    channels = client.get_channel_counts(kw)
    blog_total = int(sum(r["ratio"] for r in channels["blog"]))
    cafe_total = int(sum(r["ratio"] for r in channels["cafe"]))
    print(f"키워드 '{kw}' 최근 언급량 — 블로그 {blog_total}건 / 카페 {cafe_total}건")
    print("  날짜         blog  cafe")
    for b, c in zip(channels["blog"][-7:], channels["cafe"][-7:]):
        print(f"  {b['date']}   {int(b['ratio']):4d}  {int(c['ratio']):4d}")

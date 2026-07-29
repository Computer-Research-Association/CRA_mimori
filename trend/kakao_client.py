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
_MAX_PAGES = 10  # 소스당 최대 500건 수집 (카카오 노출 상한 = pageable_count 최대 500)


def _is_complete(meta: dict) -> bool:
    """
    카카오 응답 meta 로 '검색 결과를 전부 노출받았는지' 판단한다.
    total_count(전체 검색 수) <= pageable_count(노출 가능 수) 면 상한 초과분이
    없다는 뜻이라 완전 수집이다.

    total_count/pageable_count 가 없으면(스키마 변경·비정상 응답 등) 완전성을
    확인할 수 없다. 이때 '완전'으로 낙관하면 미수집 과거를 0으로 채워 baseline 을
    부풀리는 silent undercount 가 되므로, 확인 불가 = 불완전(보수적)으로 본다.
    """
    total = meta.get("total_count")
    pageable = meta.get("pageable_count")
    if total is None or pageable is None:
        return False
    return total <= pageable


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

    def _fetch_dates(
        self, url: str, keyword: str, cutoff: date
    ) -> tuple[list[str], bool]:
        """
        cutoff(포함) 이후 문서의 날짜(YYYY-MM-DD) 목록과 '완전 수집' 여부를 반환한다.

        반환: (dates, complete)
          - complete=True  : cutoff 까지 모든 문서를 확보했다. 창(window) 안에서
                             수집 안 된 날은 '진짜 언급 0'으로 봐도 된다.
          - complete=False : 카카오 API 하드 리밋(노출 가능 문서 최대 500건)에 걸려
                             오래된 문서를 더 못 가져왔다. 수집 못 한 과거 날은 '0'이
                             아니라 '미상'이므로, 호출부에서 baseline 에 0으로 채우면 안 된다.

        완전성 판정은 meta.total_count(전체 검색 수) vs pageable_count(노출 가능 수,
        최대 500)로 한다. total_count > pageable_count 면 API 가 못 주는 문서가 남아
        있다는 뜻이다. 주의: 카카오는 이 500 상한에 도달하면 is_end 도 True 로 주므로
        is_end 만으로는 '진짜 끝'과 '상한 포화'를 구분할 수 없다.

        recency 정렬이므로 cutoff 이전 문서가 처음 나오면 즉시 중단한다(창 전체 커버).
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
            meta = payload.get("meta", {})
            if not documents:
                return dates, _is_complete(meta)  # 더 줄 문서 없음

            for doc in documents:
                dt_str = doc.get("datetime", "")
                if not dt_str:
                    continue
                doc_date = date.fromisoformat(dt_str[:10])
                if doc_date < cutoff:
                    return dates, True  # cutoff 이전 도달 → 창 전체 커버
                dates.append(doc_date.isoformat())

            if meta.get("is_end") is True:
                # 노출 가능 문서를 다 봤다. 상한 초과분이 남아 있으면 불완전.
                # is_end 누락 시엔 끝을 단정하지 않고 계속 페이징한다(누락=미완 취급).
                return dates, _is_complete(meta)

        # _MAX_PAGES 를 다 쓰고도 cutoff/끝에 못 닿음 → 상한 포화
        return dates, False

    def _daily_series(self, url: str, keyword: str, days: int) -> list[dict]:
        """
        한 채널(블로그 or 카페)의 일별 언급 건수 시계열을 만든다.

        반환 형식: [{"date": "YYYY-MM-DD", "ratio": float}, ...]  (날짜 오름차순)
        필드명을 ratio 로 맞춰 zscore 파이프라인과 인터페이스를 통일한다.

        수집이 완전하면 창 전체(days 일)를 만들고 언급 없는 날은 0.0 으로 채운다.
        하지만 500건 상한에 걸려(complete=False) 오래된 문서를 못 가져온 경우,
        가장 오래된 수집일은 페이지 중간에서 잘렸을 수 있어 건수가 과소하고,
        그보다 더 과거는 아예 미수집이다. 이때 그 구간을 0으로 채우면 baseline 이
        인위적으로 낮아져 z 가 뻥튀기된다(인기 키워드일수록 심함). 그래서
        '완전히 관측된 최근 구간'만 series 로 만들고 불확실한 과거는 제외한다.
        (남은 날이 게이트 하한 미만이면 상위에서 카카오를 제외 → z 오염 방지.)
        """
        today = date.today()
        cutoff = today - timedelta(days=days - 1)
        fetched, complete = self._fetch_dates(url, keyword, cutoff)
        bucket: dict[str, int] = defaultdict(int)
        for d in fetched:
            bucket[d] += 1

        if complete or not fetched:
            start = cutoff
        else:
            # 가장 오래된 수집일은 잘렸을 수 있으니 그 '다음 날'부터만 완전 커버로 본다.
            oldest = date.fromisoformat(min(fetched))
            start = oldest + timedelta(days=1)

        series = []
        day = start
        while day <= today:
            key = day.isoformat()
            series.append({"date": key, "ratio": float(bucket.get(key, 0))})
            day += timedelta(days=1)
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
    여러 일별 시계열을 날짜별 ratio 합산으로 병합한다.
    baseline 게이트를 '합산 기준'으로 걸 때 사용한다.

    채널마다 커버 범위가 다를 수 있다(한 채널만 500건 상한에 포화돼 최근 구간만
    남는 경우). 이때 한쪽에만 있는 날을 그대로 더하면 그 날의 합산값이 반쪽만
    반영돼 baseline 이 왜곡된다. 그래서 모든 입력에 공통으로 존재하는 날짜
    (교집합)만 합산한다. 정상 구간(양 채널 모두 30일 커버)에서는 교집합이 곧
    전체 창이라 기존 동작과 동일하다.
    """
    if not series_list:
        return []
    date_sets = [{row["date"] for row in series} for series in series_list]
    common = set.intersection(*date_sets)
    bucket: dict[str, float] = defaultdict(float)
    for series in series_list:
        for row in series:
            if row["date"] in common:
                bucket[row["date"]] += float(row["ratio"])
    return [
        {"date": day, "ratio": bucket[day]}
        for day in sorted(common)
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

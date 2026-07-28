"""
datalab_client.py
네이버 데이터랩(검색어 트렌드) API 클라이언트.

밈/신조어의 최근 일별 검색량(상대값)을 가져와 z-score 계산의 1차 지표로 사용한다.
데이터랩은 절대 검색량이 아니라 기간 내 최댓값을 100으로 둔 '상대 비율'을 돌려준다는
점에 유의. 이 상대 비율끼리 Robust Scaling 하는 것은 문제없다(스케일 불변).
"""

import os
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

# 데이터랩 검색어 트렌드 API 엔드포인트
API_URL = "https://openapi.naver.com/v1/datalab/search"

# 데이터랩이 허용하는 가장 이른 조회 시작일
MIN_START_DATE = "2016-01-01"


class DataLabClient:
    """네이버 데이터랩 검색어 트렌드 API 래퍼."""

    def __init__(self, client_id: str = None, client_secret: str = None):
        self.client_id = client_id or os.getenv("NAVER_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("NAVER_CLIENT_SECRET", "")

        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 가 설정되지 않았습니다. "
                ".env 를 확인하세요."
            )

    def _headers(self) -> dict:
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
            "Content-Type": "application/json",
        }

    def get_recent_ratios(
        self,
        keyword: str,
        related_keywords: list[str] = None,
        days: int = 30,
        time_unit: str = "date",
    ) -> list[dict]:
        """
        최근 `days`일간의 일별 검색량 상대 비율을 반환한다.

        keyword 와 related_keywords 는 하나의 그룹으로 묶어서 요청한다.
        (밈은 "야르", "야르 뜻", "야르가 뭐야" 처럼 여러 변형으로 검색되므로
         합산해야 실제 관심도를 더 잘 반영한다. 데이터랩은 그룹당 최대 5개 키워드 허용.)

        반환 형식: [{"date": "YYYY-MM-DD", "ratio": float}, ...]  (날짜 오름차순)
        데이터랩이 생략하는 검색량 0인 날은 ratio=0.0 으로 채운다(카카오 시계열과 동일).
        """
        keywords = [keyword]
        if related_keywords:
            keywords += related_keywords
        keywords = keywords[:5]  # 데이터랩 그룹당 키워드 최대 5개

        end = date.today()
        start = end - timedelta(days=days)
        start_str = max(start.isoformat(), MIN_START_DATE)

        body = {
            "startDate": start_str,
            "endDate": end.isoformat(),
            "timeUnit": time_unit,  # "date" | "week" | "month"
            "keywordGroups": [
                {"groupName": keyword, "keywords": keywords},
            ],
        }

        resp = requests.post(
            API_URL, headers=self._headers(), json=body, timeout=10
        )
        resp.raise_for_status()
        payload = resp.json()

        results = payload.get("results", [])
        if not results:
            return []

        data = results[0].get("data", [])
        if not data:
            return []

        # 데이터랩은 검색량이 0(임계 미만)인 날을 응답에서 아예 생략한다.
        # 그대로 두면 저빈도/신생 밈은 baseline 이 '검색된 날'로만 채워져 위로 편향되고,
        # 그러면 오늘의 급등이 상대적으로 눌려 z 가 과소평가된다. 카카오 시계열처럼
        # 빠진 날을 0.0 으로 채워 baseline 이 실제 분포(0 포함)를 반영하게 한다.
        #
        # 다만 데이터랩은 1~2일 발행 지연이 있어, '아직 안 나온' 최근일을 0 으로
        # 채우면 today_value 가 가짜 0 이 될 수 있다. 그래서 채우는 범위를
        # [요청 시작일, 응답에 존재하는 최신일] 로 제한한다(그 이후는 미발행으로 간주).
        ratio_by_date = {p["period"]: float(p["ratio"]) for p in data}
        last_date = date.fromisoformat(max(ratio_by_date))
        cursor = date.fromisoformat(start_str)

        series: list[dict] = []
        while cursor <= last_date:
            iso = cursor.isoformat()
            series.append({"date": iso, "ratio": ratio_by_date.get(iso, 0.0)})
            cursor += timedelta(days=1)
        return series


if __name__ == "__main__":
    # 단독 실행 시 실제 API 호출이 되는지 확인하는 스모크 테스트
    client = DataLabClient()
    kw = "야르"
    ratios = client.get_recent_ratios(kw, related_keywords=[f"{kw} 뜻", f"{kw}가 뭐야"])
    print(f"키워드 '{kw}' 최근 검색량 데이터 {len(ratios)}건")
    for row in ratios:
        print(f"  {row['date']}  ratio={row['ratio']}")
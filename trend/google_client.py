"""
google_client.py
Google Trends(pytrends) 클라이언트 — 세 번째 트렌드 소스(google_z).

pytrends 는 비공식 라이브러리라 레이트리밋/차단이 예고 없이 발생한다.
그래서 방어 로직을 기본 탑재한다:
  1. 요청 간 랜덤 딜레이(1~2초) — 연속 키워드 조회 시 과도한 빈도 방지
  2. 실패(429 포함) 시 지수 백오프로 최대 2회 재시도
  3. 최종 실패 시 예외를 던지지 않고 None 반환 — 파이프라인이 죽지 않게
  4. 키워드별 12시간 파일 캐시 — 다른 소스보다 길게 재사용해 호출 빈도 자체를 줄임
     (네이버/카카오는 공식 API라 매 실행 조회하지만, 구글은 차단 리스크가 커서
      트렌드 변화 감지의 시의성을 조금 희생하고 안정성을 우선한다)

반환 필드명은 naver/kakao 클라이언트와 동일하게 ratio 로 맞춰
zscore 파이프라인과 인터페이스를 통일한다. 값은 데이터랩과 같은
'기간 내 최댓값=100 상대 비율'이라 robust scaling 에 그대로 쓸 수 있다.
"""

import json
import os
import random
import threading
import time
from datetime import datetime, timedelta

# 캐시 TTL. 구글만 12시간으로 길게 둔다(호출 빈도 절감).
CACHE_TTL_HOURS = 24

_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".google_trends_cache.json")
_DELAY_RANGE = (3.0, 5.0)  # 요청 전 랜덤 딜레이(초)
_MAX_RETRIES = 2  # 최초 시도 후 재시도 횟수

# 캐시는 read-modify-write(파일 전체 읽고→수정→덮어쓰기)라 병렬 실행 시 위험하다.
# 락 없이 동시 진입하면 (1) lost update 로 다른 키워드 항목이 증발하거나,
# (2) 동시에 truncate 되어 깨진 JSON 이 남고 _load_cache 가 그걸 {} 로 삼켜
# 12시간 캐시가 통째로 날아간다 → 다음 실행에서 pytrends 전 키워드 재조회 → 429 폭주.
# 그래서 _cache_set 전체를 이 락으로 묶고, 쓰기는 임시파일+os.replace 로 원자화한다.
_CACHE_LOCK = threading.Lock()


class GoogleTrendsClient:
    """Google Trends 검색 관심도로 키워드 시계열을 구성한다."""

    def __init__(self, hl: str = "ko", tz: int = 540):
        # hl=언어, tz=분 단위 UTC 오프셋(한국 = +9h = 540)
        self.hl = hl
        self.tz = tz
        self._pytrends = None  # 지연 초기화(임포트 실패도 요청 실패로 처리)

    def _client(self):
        if self._pytrends is None:
            from pytrends.request import TrendReq
            self._pytrends = TrendReq(hl=self.hl, tz=self.tz)
        return self._pytrends

    def get_recent_ratios(self, keyword: str) -> list[dict] | None:
        """
        최근 30일(today 1-m)의 일별 검색 관심도를 반환한다.

        반환 형식: [{"date": "YYYY-MM-DD", "ratio": float}, ...]  (날짜 오름차순)
        - 캐시(12h)가 유효하면 API 호출 없이 캐시를 반환한다.
        - 데이터가 없으면 [](빈 시리즈), 요청 실패면 None.
          둘 다 상위(trend_service)에서 google_z 제외로 처리된다.
        """
        cached = _cache_get(keyword)
        if cached is not None:
            return cached

        series = self._fetch(keyword)
        if series is not None:
            _cache_set(keyword, series)
        return series

    def _fetch(self, keyword: str) -> list[dict] | None:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                time.sleep(random.uniform(*_DELAY_RANGE))
                pt = self._client()
                pt.build_payload([keyword], timeframe="today 1-m")
                df = pt.interest_over_time()

                if df is None or df.empty:
                    return []  # 검색량 자체가 없는 키워드

                # isPartial=True 는 집계가 끝나지 않은 구간(보통 최신일).
                # 미완성 값이 z 판정을 오염시키지 않게 클라이언트에서 걸러낸다.
                if "isPartial" in df.columns:
                    df = df[~df["isPartial"].astype(bool)]

                return [
                    {"date": idx.strftime("%Y-%m-%d"), "ratio": float(row[keyword])}
                    for idx, row in df.iterrows()
                ]

            except Exception as exc:  # noqa: BLE001 - 429 포함 모든 실패를 재시도 대상으로
                if attempt < _MAX_RETRIES:
                    backoff = 2 ** (attempt + 1)  # 2초 → 4초
                    print(
                        f"[google_client] 요청 실패({exc!r}), "
                        f"{backoff}초 후 재시도 ({attempt + 1}/{_MAX_RETRIES})"
                    )
                    time.sleep(backoff)
                else:
                    print(f"[google_client] 최대 재시도 초과, google_z 제외: {exc!r}")

        return None


# ── 파일 캐시 (키워드별 12시간 재사용) ────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _cache_get(keyword: str) -> list[dict] | None:
    entry = _load_cache().get(keyword)
    if not entry:
        return None
    fetched_at = datetime.fromisoformat(entry["fetched_at"])
    if datetime.now() - fetched_at > timedelta(hours=CACHE_TTL_HOURS):
        return None  # 만료 → 재조회
    return entry["series"]


def _cache_set(keyword: str, series: list[dict]) -> None:
    with _CACHE_LOCK:  # read-modify-write 를 원자 구간으로 (lost update / 파일 손상 방지)
        cache = _load_cache()
        cache[keyword] = {
            "fetched_at": datetime.now().isoformat(),
            "series": series,
        }
        tmp_path = f"{_CACHE_PATH}.tmp"
        try:
            # 임시파일에 먼저 쓰고 os.replace 로 원자적 교체 → 중간에 죽어도 기존
            # 캐시 파일은 손상되지 않는다(부분 쓰기로 깨진 JSON 이 남지 않음).
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
            os.replace(tmp_path, _CACHE_PATH)
        except OSError as exc:
            # 캐시 저장 실패는 치명적이지 않다(다음 실행에서 재조회할 뿐)
            print(f"[google_client] 캐시 저장 실패(무시): {exc}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    kw = input("키워드 입력: ").strip()
    client = GoogleTrendsClient()
    ratios = client.get_recent_ratios(kw)
    if ratios is None:
        print("요청 실패 (google_z 제외 대상)")
    else:
        print(f"키워드 '{kw}' 최근 관심도 {len(ratios)}건")
        for row in ratios[-7:]:
            print(f"  {row['date']}  ratio={row['ratio']}")
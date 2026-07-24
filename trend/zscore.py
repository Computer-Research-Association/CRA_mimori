"""
zscore.py
Robust Scaling(중앙값 / IQR) 기반 유행 상태 판정 로직.

일반 z-score(평균/표준편차)는 검색량 급등 같은 이상치에 민감해서
기준선 자체가 흔들린다. 그래서 중앙값과 IQR을 쓰는 Robust Scaling으로
'오늘 값'이 최근 기준선 대비 얼마나 벗어났는지를 z값으로 환산한다.
"""

import statistics
from datetime import date

# IQR 최소 바닥값.
# 저빈도 키워드는 baseline 표본이 적어 IQR이 0에 가깝게 나오고,
# 그러면 z = (today - median) / IQR 이 20 이상으로 폭발한다.
# 분모에 바닥값을 둬 이 불안정성을 원인 단계에서 억제한다(클램핑 대신).
# 상수로 분리해 실측 후 튜닝 가능하게 둔다.
MIN_IQR = 2.0


def drop_incomplete_today(daily_ratios: list[dict]) -> list[dict]:
    """
    시계열에서 '오늘'(달력상 아직 끝나지 않은 당일) 데이터를 제거한다.

    네이버/카카오 모두 API가 돌려주는 당일 값은 하루가 끝나기 전이라
    부분 집계다. 이걸 today_value 로 쓰면 어제 급등한 밈이 '감소'로
    잘못 판정된다. 그래서 판정에는 완성된 최근일만 쓴다.

    스케줄러 실행 시각을 우리가 제어할 수 없으므로(타이밍 비의존),
    당일 제거로 항상 완성된 날을 비교 대상으로 삼는다.
    """
    today = date.today().isoformat()
    return [row for row in daily_ratios if row["date"] < today]


def robust_zscore(
    values: list[float], today_value: float, min_iqr: float = MIN_IQR
) -> float:
    """
    baseline `values` 대비 `today_value` 의 robust z값을 계산한다.
    naver_z, kakao_z 가 공통으로 쓰는 공유 유틸.

        iqr_safe = max(IQR, min_iqr)
        z = (today_value - median) / iqr_safe

    IQR(=Q3-Q1)이 0이거나 비정상적으로 작아도 min_iqr 바닥값 덕분에
    z가 폭발하지 않는다. (분모가 0이 되던 기존 반환 0.0 가드는 불필요해져 제거.)
    baseline이 2개 미만이면 분위수를 구할 수 없어 0.0을 반환한다.
    """
    if len(values) < 2:
        return 0.0

    median = statistics.median(values)

    # quantiles(n=4) -> [Q1, Q2, Q3]
    q1, _, q3 = statistics.quantiles(values, n=4)
    iqr = q3 - q1
    iqr_safe = max(iqr, min_iqr)

    return (today_value - median) / iqr_safe


def classify_trend(z: float) -> str:
    """robust z값을 유행 상태 라벨로 변환."""
    if z > 2:
        return "핫함"
    if z >= 0:
        return "유행 중"
    if z >= -2:
        return "감소"
    return "소멸"


def zscore_from_series(daily_ratios: list[dict]) -> float:
    """
    일별 시계열에서 robust z값만 계산해 반환한다.

    입력: [{"date": "YYYY-MM-DD", "ratio": float}, ...]
    가장 최신 날짜의 ratio를 today_value, 나머지를 baseline으로 사용한다.
    데이터가 없으면 0.0(중립)을 반환한다.

    datalab/google trends(검색량 상대값)와 kakao(언급 건수) 모두 이 함수로
    처리한다(내부적으로 동일한 robust_zscore + MIN_IQR 바닥값 적용) -
    robust scaling 은 스케일 불변이라 모든 소스를 같은 z 단위로 환산해준다.
    """
    if not daily_ratios:
        return 0.0

    # 날짜 오름차순 정렬 후 마지막(최신)을 today로
    ordered = sorted(daily_ratios, key=lambda row: row["date"])
    today_value = float(ordered[-1]["ratio"])
    baseline = [float(row["ratio"]) for row in ordered[:-1]]

    return robust_zscore(baseline, today_value)


def get_zscore(daily_ratios: list[dict]) -> tuple[float, str]:
    """
    datalab_client.get_recent_ratios() 결과를 받아 (z값, 상태라벨)을 반환한다.

    입력: [{"date": "YYYY-MM-DD", "ratio": float}, ...]
    가장 최신 날짜의 ratio를 today_value, 나머지를 baseline으로 사용한다.
    """
    z = zscore_from_series(daily_ratios)
    return z, classify_trend(z)
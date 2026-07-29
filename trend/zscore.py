"""
zscore.py
Robust Scaling(중앙값 / IQR) 기반 유행 상태 판정 로직.

일반 z-score(평균/표준편차)는 검색량 급등 같은 이상치에 민감해서
기준선 자체가 흔들린다. 그래서 중앙값과 IQR을 쓰는 Robust Scaling으로
'오늘 값'이 최근 기준선 대비 얼마나 벗어났는지를 z값으로 환산한다.
"""

import statistics


def robust_scale(values: list[float], today_value: float) -> float:
    """
    baseline `values` 대비 `today_value` 의 robust z값을 계산한다.

        z = (today_value - median) / IQR

    IQR(=Q3-Q1)이 0이면 분모가 0이 되므로 0.0을 반환한다.
    baseline이 2개 미만이면 분위수를 구할 수 없어 0.0을 반환한다.
    """
    if len(values) < 2:
        return 0.0

    median = statistics.median(values)

    # quantiles(n=4) -> [Q1, Q2, Q3]
    q1, _, q3 = statistics.quantiles(values, n=4)
    iqr = q3 - q1
    if iqr == 0:
        return 0.0

    return (today_value - median) / iqr


def classify_trend(z: float) -> str:
    """robust z값을 유행 상태 라벨로 변환."""
    if z > 2:
        return "핫함"
    if z >= 0:
        return "유행 중"
    if z >= -2:
        return "감소"
    return "소멸"


def get_zscore(daily_ratios: list[dict]) -> tuple[float, str]:
    """
    datalab_client.get_recent_ratios() 결과를 받아 (z값, 상태라벨)을 반환한다.

    입력: [{"date": "YYYY-MM-DD", "ratio": float}, ...]
    가장 최신 날짜의 ratio를 today_value, 나머지를 baseline으로 사용한다.
    """
    if not daily_ratios or len(daily_ratios) < 3:
        # baseline이 2개 미만이면 robust_scale이 0.0을 반환해 항상 "유행 중"으로
        # 오판정된다. 데이터가 충분하지 않으면 판정 보류("데이터 부족") 반환.
        return 0.0, "데이터 부족"

    # 날짜 오름차순 정렬 후 마지막(최신)을 today로
    ordered = sorted(daily_ratios, key=lambda row: row["date"])
    today_value = float(ordered[-1]["ratio"])
    baseline = [float(row["ratio"]) for row in ordered[:-1]]

    z = robust_scale(baseline, today_value)
    return z, classify_trend(z)
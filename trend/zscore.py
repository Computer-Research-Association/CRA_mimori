"""
zscore.py
Robust Scaling(중앙값 / IQR) 기반 유행 상태 판정 로직.

일반 z-score(평균/표준편차)는 검색량 급등 같은 이상치에 민감해서
기준선 자체가 흔들린다. 그래서 중앙값과 IQR을 쓰는 Robust Scaling으로
'오늘 값'이 최근 기준선 대비 얼마나 벗어났는지를 z값으로 환산한다.
"""

import statistics
from datetime import date

# IQR 최소 바닥값(기본값).
# 저빈도 키워드는 baseline 표본이 적어 IQR이 0에 가깝게 나오고,
# 그러면 z = (today - median) / IQR 이 20 이상으로 폭발한다.
# 분모에 바닥값을 둬 이 불안정성을 원인 단계에서 억제한다(클램핑 대신).
#
# 주의: robust scaling 자체는 스케일 불변이지만, 이 '절대 상수' 바닥값이
# 실제로 걸리는 구간(저빈도)에서는 분모가 데이터 스케일이 아니라 상수로
# 고정되므로 스케일 불변성이 깨진다. 즉 0~100 상대값(네이버/구글)과
# 언급 건수(카카오)에 같은 바닥값을 쓰면 저빈도 구간의 z 스케일이 어긋난다.
# → 소스별로 다른 min_iqr 을 주입할 수 있도록 zscore_from_series 가 파라미터로 받는다.
#   (이 값은 그 파라미터의 기본값일 뿐. 소스별 튜닝은 trend_service 의 *_MIN_IQR 참고.)
MIN_IQR = 2.0

# classify_trend 의 중립(평상) 밴드 반폭.
# |z| < NEUTRAL_BAND 이면 오늘 값이 baseline 중앙값과 사실상 같다는 뜻이라
# '유행'도 '감소'도 아닌 '평상'으로 본다. z=0(변화 없음 / 무신호 폴백)이
# "유행 중"으로 새는 것도 이 밴드가 함께 막는다.
NEUTRAL_BAND = 0.5

# 활성 소스가 하나도 없을 때의 상태 라벨.
# zscore_from_series 는 데이터가 없거나 baseline 이 부족하면 0.0(중립)을 반환하는데,
# classify_trend(0.0) 은 이제 중립 밴드에 걸려 "평상"이 된다. 그래도 '무신호'와
# '진짜 평상'은 의미가 다르므로, 활성 소스 0개는 판정 자체를 보류하는 별도 라벨로 구분한다.
STATUS_INSUFFICIENT = "데이터 부족"


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

    # quantiles(n=4, method="inclusive") -> [Q1, Q2, Q3]
    # 기본값 method="exclusive" 는 표본이 적을 때 Q1/Q3 를 데이터 범위 밖으로
    # 외삽해 IQR 을 과대평가하고, 그러면 z가 실제보다 눌린다. baseline 이 짧은
    # 보조 소스(카카오/구글)를 고려해 데이터 범위 안에서만 보간하는 inclusive 로 고정한다.
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1
    iqr_safe = max(iqr, min_iqr)

    return (today_value - median) / iqr_safe


def classify_trend(z: float) -> str:
    """robust z값을 유행 상태 라벨로 변환."""
    if z > 2:
        return "핫함"
    if z >= NEUTRAL_BAND:
        return "유행 중"
    if z > -NEUTRAL_BAND:
        # |z| < NEUTRAL_BAND: baseline 중앙값과 사실상 동일 → 변화 없음.
        return "평상"
    if z >= -2:
        return "감소"
    return "소멸"


def zscore_from_series(daily_ratios: list[dict], min_iqr: float = MIN_IQR) -> float:
    """
    일별 시계열에서 robust z값만 계산해 반환한다.

    입력: [{"date": "YYYY-MM-DD", "ratio": float}, ...]
    가장 최신 날짜의 ratio를 today_value, 나머지를 baseline으로 사용한다.
    데이터가 없으면 0.0(중립)을 반환한다.

    datalab/google trends(검색량 상대값)와 kakao(언급 건수) 모두 이 함수로
    처리한다. robust scaling 은 IQR 이 분모로 실제 쓰이는 정상 구간에서는
    스케일 불변이지만, min_iqr 바닥값이 걸리는 저빈도 구간에서는 스케일 불변성이
    깨진다. 그래서 소스별 스케일에 맞는 min_iqr 을 호출부에서 주입받는다
    (기본값은 MIN_IQR; 소스별 값은 trend_service 의 *_MIN_IQR 참고).
    """
    if not daily_ratios:
        return 0.0

    # 날짜 오름차순 정렬 후 마지막(최신)을 today로
    ordered = sorted(daily_ratios, key=lambda row: row["date"])
    today_value = float(ordered[-1]["ratio"])
    baseline = [float(row["ratio"]) for row in ordered[:-1]]

    return robust_zscore(baseline, today_value, min_iqr=min_iqr)


def get_zscore(
    daily_ratios: list[dict], min_iqr: float = MIN_IQR
) -> tuple[float, str]:
    """
    datalab_client.get_recent_ratios() 결과를 받아 (z값, 상태라벨)을 반환한다.

    입력: [{"date": "YYYY-MM-DD", "ratio": float}, ...]
    가장 최신 날짜의 ratio를 today_value, 나머지를 baseline으로 사용한다.
    """
    z = zscore_from_series(daily_ratios, min_iqr=min_iqr)
    return z, classify_trend(z)

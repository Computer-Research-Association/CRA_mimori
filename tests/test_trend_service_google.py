"""
trend_service.get_meme_trend()의 구글 트렌드 저빈도(이봉 분포) 판단 로직 단위 테스트.
실제 API 호출 없이 _safe_*_ratios/_safe_kakao_channels를 monkeypatch로 대체한다.

배경: 실측 사례('젬민이', 2026-09-10) — google_ratios 최근 30일 중 22일이 0이고
나머지 8일만 80~100대로 튀는 이봉(bimodal) 분포였는데, baseline 산술평균(20.7)은
GOOGLE_MIN_BASELINE_AVG(10)를 가볍게 통과했다. 이런 분포는 median=0, IQR≈0으로
무너져 GOOGLE_MIN_IQR(2.0) 바닥값에 걸리고, 오늘 값 하나만으로 z=49 같은 값이
나온다. naver_z는 그날 오히려 음수(-0.79, 평상~감소 수준)였는데도 google_z가
끌어올려 final_z가 FINAL_Z_CLIP(8.0)에 걸려 '핫함'으로 오판정됐다. baseline 중
값이 있는 날의 비율(GOOGLE_MIN_NONZERO_FRACTION)을 추가로 게이트해 고쳤다.

실행: uv run python tests/test_trend_service_google.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trend.trend_service as trend_service


def _dates_back(n, start_offset=1):
    """오늘로부터 start_offset일 전부터 과거로 n일치 날짜 문자열 목록(오래된 순)."""
    today = date.today()
    return [(today - timedelta(days=start_offset + i)).isoformat() for i in range(n)][::-1]


def _series(dates, value):
    return [{"date": d, "ratio": value} for d in dates]


def _patch(naver=None, kakao=None, google=None):
    """세 소스를 한 번에 monkeypatch하고 원본을 반환(복원용)."""
    original = (
        trend_service._safe_naver_ratios,
        trend_service._safe_kakao_channels,
        trend_service._safe_google_ratios,
    )
    trend_service._safe_naver_ratios = naver
    trend_service._safe_kakao_channels = kakao
    trend_service._safe_google_ratios = google
    return original


def _restore(original):
    (
        trend_service._safe_naver_ratios,
        trend_service._safe_kakao_channels,
        trend_service._safe_google_ratios,
    ) = original


# 네이버는 항상 신호 있는 것으로 고정 — 네이버 무신호면 카카오/구글이 뭘 하든
# "데이터 부족"으로 판정이 보류돼(get_meme_trend 자체 규칙) 구글 로직을 테스트할 수 없다.
# naver_z가 음수(살짝 하락)가 나오게 마지막날만 낮춰서, 구글 게이트 실패 시 '핫함'으로
# 오판정되지 않는다는 것까지 함께 확인한다(실제 '젬민이' 사례와 같은 모양).
def _naver_slightly_down(keyword, related_keywords):
    dates = _dates_back(20)
    ratios = _series(dates[:-1], 60.0) + _series([dates[-1]], 40.0)
    return ratios


# 카카오는 항상 무신호로 고정 — 이 테스트는 구글 게이트만 본다.
def _kakao_no_signal(keyword):
    return {"blog": [], "cafe": []}


def test_이봉_분포는_평균이_높아도_sparse_signal로_제외된다():
    """대부분 0이다가 소수만 스파이크(실측 '젬민이' 패턴 축소 재현): baseline 30일 중
    7일만 값이 있어도 그 값들이 크면 평균은 쉽게 GOOGLE_MIN_BASELINE_AVG(10)를 넘는다."""
    dates = _dates_back(30)
    zero_days, spike_days = dates[:23], dates[23:]  # 23일 0, 7일 스파이크
    google_ratios = _series(zero_days, 0.0) + _series(spike_days, 90.0)

    original = _patch(
        naver=_naver_slightly_down,
        kakao=_kakao_no_signal,
        google=lambda keyword: google_ratios,
    )
    try:
        result = trend_service.get_meme_trend("테스트키워드")
        assert "google" not in result["sources"], result["sources"]
        assert "google_excluded_sparse_signal" in result["ensemble_note"], result["ensemble_note"]
        # google_z 자체는 진단용으로 계속 계산·반환된다(제외 사유 추적용) — 다만 앙상블엔 안 들어감.
        assert result["google_z"] != 0.0, result["google_z"]
        # 구글이 빠졌으니 최종 판정은 네이버(소폭 하락)만 반영해야 한다 — '핫함'으로 안 튐.
        assert result["status"] != "핫함", result
    finally:
        _restore(original)
    print("[OK] baseline 평균이 높아도 값 있는 날 비율이 낮으면 sparse_signal로 제외")


def test_고르게_분포된_신호는_평균만_넘으면_그대로_사용된다():
    """회귀 테스트 — 매일 고르게 값이 있는(스파이크가 아닌) 정상 케이스는 여전히 채택돼야 한다."""
    dates = _dates_back(30)
    google_ratios = _series(dates, 50.0)  # 30일 내내 고르게 50

    original = _patch(
        naver=_naver_slightly_down,
        kakao=_kakao_no_signal,
        google=lambda keyword: google_ratios,
    )
    try:
        result = trend_service.get_meme_trend("테스트키워드")
        assert "google" in result["sources"], result["sources"]
        assert "google_excluded" not in result["ensemble_note"], result["ensemble_note"]
    finally:
        _restore(original)
    print("[OK] 고르게 분포된 정상 신호는 회귀 없이 그대로 채택됨")


if __name__ == "__main__":
    test_이봉_분포는_평균이_높아도_sparse_signal로_제외된다()
    test_고르게_분포된_신호는_평균만_넘으면_그대로_사용된다()
    print("\nALL PASS ✅")

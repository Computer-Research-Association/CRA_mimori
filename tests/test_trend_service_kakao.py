"""
trend_service.get_meme_trend()의 카카오 채널 판단 로직 단위 테스트.
실제 API 호출 없이 _safe_*_ratios/_safe_kakao_channels를 monkeypatch로 대체한다.

배경: merge_daily_series가 blog/cafe의 '교집합 날짜'만 합산하는데, 신호 유무 판단을
이 교집합 기준으로 했더니 한쪽 채널이 원래 짧기만 해도(예: blog 1일치) 다른 채널의
멀쩡한 데이터(cafe 19일치)까지 통째로 버려져 카카오가 사실상 항상 무신호 처리되던
버그가 있었다. 이 테스트는 그 수정(채널별 독립 판단)을 고정한다.

실행: uv run python tests/test_trend_service_kakao.py
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


# 네이버는 항상 신호 있는 것으로 고정 — 네이버 무신호면 카카오가 뭘 하든
# "데이터 부족"으로 판정이 보류돼(get_meme_trend 자체 규칙) 카카오 로직을 테스트할 수 없다.
def _naver_with_signal(keyword, related_keywords):
    return _series(_dates_back(10), 50.0)


def test_한쪽_채널만_짧아도_다른_채널_신호는_살아남는다():
    """실제 재현됐던 버그 시나리오: 블로그는 딱 오늘 하루뿐(=drop_incomplete_today로
    사라짐), 카페는 19일치 정상 데이터. 고치기 전엔 교집합이 1일 미만이라 카카오
    전체가 kakao_excluded_no_signal 이었다. 고친 뒤엔 카페 신호만으로 카카오가 살아야 한다.
    """
    blog = _series([date.today().isoformat()], 3.0)  # 오늘 하루뿐 -> drop_incomplete_today가 지움
    cafe = _series(_dates_back(19), 15.0)  # 19일치, 평균 15 (>= KAKAO_MIN_BASELINE_AVG=10)

    original = _patch(
        naver=_naver_with_signal,
        kakao=lambda keyword: {"blog": blog, "cafe": cafe},
        google=lambda keyword: None,
    )
    try:
        result = trend_service.get_meme_trend("테스트키워드")
        assert "kakao" in result["sources"], result["sources"]
        assert "kakao_excluded_no_signal" not in result["ensemble_note"], result["ensemble_note"]
        # 블로그는 비활성(오늘만 있다가 drop됨)이라 kakao_z는 cafe_z를 그대로 반영해야 한다.
        assert result["kakao_z"] == result["cafe_z"], (result["kakao_z"], result["cafe_z"])
        assert result["blog_z"] == 0.0, result["blog_z"]
    finally:
        _restore(original)
    print("[OK] 블로그가 짧아도(오늘 하루뿐) 카페의 19일치 신호는 살아남는다")


def test_두_채널_다_신호_없으면_여전히_제외된다():
    """회귀 테스트 — 진짜로 둘 다 조용한 경우는 고친 뒤에도 여전히 제외돼야 한다."""
    blog = _series([date.today().isoformat()], 1.0)
    cafe = _series(_dates_back(3), 2.0)  # 3일치뿐 -> MIN_SERIES_POINTS(7) 미달

    original = _patch(
        naver=_naver_with_signal,
        kakao=lambda keyword: {"blog": blog, "cafe": cafe},
        google=lambda keyword: None,
    )
    try:
        result = trend_service.get_meme_trend("테스트키워드")
        assert "kakao" not in result["sources"], result["sources"]
        assert "kakao_excluded_no_signal" in result["ensemble_note"], result["ensemble_note"]
    finally:
        _restore(original)
    print("[OK] 두 채널 다 신호 부족이면 여전히 제외됨(회귀 없음)")


def test_두_채널_다_활성이면_가중_평균으로_합쳐진다():
    """블로그/카페 둘 다 충분한 데이터가 있으면 각각 0.5 가중치로 평균낸 값이 kakao_z."""
    blog = _series(_dates_back(10), 20.0)
    cafe = _series(_dates_back(10), 20.0)

    original = _patch(
        naver=_naver_with_signal,
        kakao=lambda keyword: {"blog": blog, "cafe": cafe},
        google=lambda keyword: None,
    )
    try:
        result = trend_service.get_meme_trend("테스트키워드")
        assert "kakao" in result["sources"], result["sources"]
        expected = (result["blog_z"] + result["cafe_z"]) / 2
        assert abs(result["kakao_z"] - expected) < 1e-9, (result["kakao_z"], expected)
    finally:
        _restore(original)
    print("[OK] 두 채널 다 활성이면 0.5/0.5 가중 평균")


def test_baseline_평균이_낮으면_신호는_있어도_low_baseline으로_제외된다():
    """MIN_SERIES_POINTS는 채우지만 평균 언급량이 KAKAO_MIN_BASELINE_AVG(10) 미만인 경우."""
    blog = _series([date.today().isoformat()], 1.0)
    cafe = _series(_dates_back(10), 2.0)  # 10일치라 신호는 있지만 평균 2 < 10

    original = _patch(
        naver=_naver_with_signal,
        kakao=lambda keyword: {"blog": blog, "cafe": cafe},
        google=lambda keyword: None,
    )
    try:
        result = trend_service.get_meme_trend("테스트키워드")
        assert "kakao" not in result["sources"], result["sources"]
        assert "kakao_excluded_low_baseline" in result["ensemble_note"], result["ensemble_note"]
    finally:
        _restore(original)
    print("[OK] 신호는 있어도 평균 언급량 낮으면 low_baseline으로 별도 제외")


if __name__ == "__main__":
    test_한쪽_채널만_짧아도_다른_채널_신호는_살아남는다()
    test_두_채널_다_신호_없으면_여전히_제외된다()
    test_두_채널_다_활성이면_가중_평균으로_합쳐진다()
    test_baseline_평균이_낮으면_신호는_있어도_low_baseline으로_제외된다()
    print("\nALL PASS ✅")

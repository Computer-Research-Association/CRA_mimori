"""
trend_service.get_meme_trend() 판정 회귀 테스트 (고정 fixture 기반).

tests/fixtures/trend_cases.json 의 사례마다 소스별 시계열을 주입해 판정 결과를 검증한다.
임계값(NAVER_MIN_IQR, FINAL_Z_CLIP, *_MIN_BASELINE_AVG 등)을 조정할 때, 과거에 사고가 났던
패턴(저빈도 -8 클리핑, 구글 이봉 오판정 등)이 다시 터지지 않는지 한 번에 확인하는 용도다.

시계열은 "오래된 순 → 어제" 값 목록으로 저장하고, 날짜는 실행 시점 기준으로 붙인다
(drop_incomplete_today 가 달력상 오늘을 제외하므로 고정 날짜를 쓰면 안 된다).
외부 API 호출은 없다 — _safe_*_ratios/_safe_kakao_channels 를 monkeypatch 로 대체한다.

사례 추가: fixture 에 항목을 더하고 expect 에 검증할 필드만 적는다.
  status / not_status / sources / flags / final_z_min / final_z_max / note_contains

실행: uv run python tests/test_trend_regression.py
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trend.trend_service as trend_service

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "trend_cases.json")


def _to_series(values):
    """값 목록(오래된 순, 마지막이 어제)에 날짜를 붙인다."""
    today = date.today()
    n = len(values)
    return [
        {"date": (today - timedelta(days=n - i)).isoformat(), "ratio": float(v)}
        for i, v in enumerate(values)
    ]


def _run_case(case):
    naver = _to_series(case["naver"])
    extra = case.get("extra_today", {})
    if "naver" in extra:
        naver.append({"date": date.today().isoformat(), "ratio": extra["naver"]})

    blog = _to_series(case["blog"])
    cafe = _to_series(case["cafe"])
    google = None if case["google"] is None else _to_series(case["google"])

    original = (
        trend_service._safe_naver_ratios,
        trend_service._safe_kakao_channels,
        trend_service._safe_google_ratios,
    )
    trend_service._safe_naver_ratios = lambda keyword, related: naver
    trend_service._safe_kakao_channels = lambda keyword: {"blog": blog, "cafe": cafe}
    trend_service._safe_google_ratios = lambda keyword: google
    try:
        return trend_service.get_meme_trend(case["name"])
    finally:
        (
            trend_service._safe_naver_ratios,
            trend_service._safe_kakao_channels,
            trend_service._safe_google_ratios,
        ) = original


def _check(case, result):
    """기대값과 어긋난 항목을 문자열 목록으로 반환한다(비어 있으면 통과)."""
    expect = case["expect"]
    problems = []

    if "status" in expect and result["status"] != expect["status"]:
        problems.append(f"status {result['status']!r} != {expect['status']!r}")
    if "not_status" in expect and result["status"] == expect["not_status"]:
        problems.append(f"status 가 {expect['not_status']!r} 이면 안 됨")
    if "sources" in expect and result["sources"] != expect["sources"]:
        problems.append(f"sources {result['sources']} != {expect['sources']}")
    for flag in expect.get("flags", []):
        if flag not in result["flags"]:
            problems.append(f"flag {flag!r} 없음 (실제 {result['flags']})")
    if "final_z_min" in expect and result["final_z"] < expect["final_z_min"]:
        problems.append(f"final_z {result['final_z']:.2f} < 하한 {expect['final_z_min']}")
    if "final_z_max" in expect and result["final_z"] > expect["final_z_max"]:
        problems.append(f"final_z {result['final_z']:.2f} > 상한 {expect['final_z_max']}")
    for part in expect.get("note_contains", []):
        if part not in result["ensemble_note"]:
            problems.append(f"ensemble_note {result['ensemble_note']!r} 에 {part!r} 없음")

    return problems


def test_트렌드_판정_회귀_사례_전부_통과한다():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    failures = []
    for case in cases:
        problems = _check(case, _run_case(case))
        if problems:
            failures.append(f"[{case['name']}] " + "; ".join(problems) + f"\n    사유: {case['why']}")

    assert not failures, "회귀 사례 실패:\n" + "\n".join(failures)


def test_fixture_사례_이름은_중복되지_않는다():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        names = [c["name"] for c in json.load(f)]
    assert len(names) == len(set(names)), f"중복 사례 이름: {names}"


if __name__ == "__main__":
    test_트렌드_판정_회귀_사례_전부_통과한다()
    test_fixture_사례_이름은_중복되지_않는다()
    print("ok")

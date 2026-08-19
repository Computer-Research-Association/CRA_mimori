"""
datalab_client.DataLabClient.get_recent_ratios()의 빈-날짜 채우기 경계 테스트.

배경: 네이버 데이터랩은 검색량 0인 날을 응답에서 생략한다. 예전 코드는 채우는
마지막 날짜를 '응답에 남은 최신 날짜'로 잡아서, 최근 며칠간 진짜로 검색량이 0이라
응답에서 생략된 경우 몇 주 전의 옛날 스파이크가 today_value로 둔갑해 z-score가
폭발했다(실측 사례: 내또출 naver_z=+22.73, 화면엔 final_z 클리핑으로 +8.00 표시).
이 테스트는 그 수정(채우기 경계를 발행지연 기준으로 오늘 쪽에 고정)을 고정한다.

실행: uv run python tests/test_datalab_client_fill.py
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trend.datalab_client as datalab_client


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _patch_post(payload):
    original = datalab_client.requests.post
    datalab_client.requests.post = lambda *a, **k: _FakeResponse(payload)
    return original


def _restore_post(original):
    datalab_client.requests.post = original


def test_최근_며칠_진짜_0이면_옛날_스파이크가_오늘값으로_둔갑하지_않는다():
    """실제 재현됐던 버그 시나리오: 6일 전 스파이크 이후로 검색량이 계속 0이라
    최근 며칠이 응답에서 통째로 생략됨. 고치기 전엔 6일 전 값이 today_value가 됐다.
    """
    today = date.today()
    spike_date = today - timedelta(days=6)
    client = datalab_client.DataLabClient(client_id="x", client_secret="y")

    payload = {
        "results": [
            {"data": [{"period": spike_date.isoformat(), "ratio": 45.45454}]}
        ]
    }
    original = _patch_post(payload)
    try:
        series = client.get_recent_ratios("테스트키워드", days=30)
    finally:
        _restore_post(original)

    by_date = {row["date"]: row["ratio"] for row in series}
    lag_cutoff = today - timedelta(days=datalab_client.PUBLISH_LAG_DAYS)

    # 스파이크 이후, 발행지연 이전 구간은 '진짜 0'으로 채워져 있어야 한다(잘리면 안 됨).
    assert (spike_date + timedelta(days=1)).isoformat() in by_date, sorted(by_date)[-5:]
    assert by_date[lag_cutoff.isoformat()] == 0.0, by_date[lag_cutoff.isoformat()]
    # 가장 최신 값(today_value)은 6일 전 스파이크가 아니라 발행지연 컷오프의 0.0이어야 한다.
    latest = max(by_date)
    assert by_date[latest] == 0.0, (latest, by_date[latest])
    assert latest != spike_date.isoformat(), "옛날 스파이크가 여전히 마지막 값으로 남아있음(버그 재현)"
    print("[OK] 오래된 스파이크가 today_value로 둔갑하지 않고 최근 구간은 정직하게 0으로 채워짐")


def test_최근에도_실제_신호가_있으면_그_날짜까지_채운다():
    """응답의 최신 날짜가 발행지연 컷오프보다 늦으면(=최근에도 검색량 있었음) 그 날짜까지 채운다."""
    today = date.today()
    recent_date = today - timedelta(days=1)  # 발행지연(2일)보다 최근
    client = datalab_client.DataLabClient(client_id="x", client_secret="y")

    payload = {"results": [{"data": [{"period": recent_date.isoformat(), "ratio": 30.0}]}]}
    original = _patch_post(payload)
    try:
        series = client.get_recent_ratios("테스트키워드", days=30)
    finally:
        _restore_post(original)

    by_date = {row["date"]: row["ratio"] for row in series}
    assert max(by_date) == recent_date.isoformat(), max(by_date)
    assert by_date[recent_date.isoformat()] == 30.0
    print("[OK] 최근 실제 신호가 있으면 그 날짜까지 정상적으로 채워짐")


if __name__ == "__main__":
    test_최근_며칠_진짜_0이면_옛날_스파이크가_오늘값으로_둔갑하지_않는다()
    test_최근에도_실제_신호가_있으면_그_날짜까지_채운다()
    print("\nALL PASS ✅")

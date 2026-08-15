"""
rate_limit.py
IP당 정해진 시간창(window) 안의 요청 횟수를 제한하는 최소 구현.

crawlers/base.py의 RateLimiter(간격을 두고 순서대로 통과시키는 스로틀)와 목적이
다르다 — 여긴 "창 안에서 N번 넘으면 그냥 거절"이 목적이라 별도로 둔다.

gunicorn이 -w 1(워커 1개, docker-compose.yml 참고)로 뜨므로 프로세스 하나 안의
dict만으로 충분하고, Redis 등 별도 저장소가 필요 없다. 워커를 여러 개로
늘리면(-w > 1) 이 가정이 깨지므로 그때는 공유 저장소 기반으로 다시 만들어야 한다.
"""
import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def allow(bucket: str, key: str, max_requests: int, window_seconds: float) -> bool:
    """(bucket, key) 조합이 최근 window_seconds 안에 max_requests번을 넘지 않았으면
    이번 호출을 기록하고 True, 넘었으면 기록하지 않고 False를 반환한다."""
    now = time.monotonic()
    with _lock:
        hits = _hits[(bucket, key)]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= max_requests:
            return False
        hits.append(now)
        return True


def reset() -> None:
    """테스트 전용: 모든 카운터를 지운다."""
    with _lock:
        _hits.clear()

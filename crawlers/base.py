"""
base.py
모든 웹 크롤러가 공통으로 사용하는 유틸리티 모음.

핵심 역할:
  1. random_delay()   — 요청 사이에 불규칙한 대기 시간을 넣어 봇처럼 보이지 않게 함
  2. make_session()   — 브라우저 흉내 헤더가 세팅된 Session 객체 생성
  3. safe_get()       — 딜레이 + 재시도(exponential backoff) + 차단 감지가 포함된 GET 요청
  4. is_blocked()     — 서버가 조용히 차단했을 때를 감지 (CAPTCHA, 로그인 페이지 등)
"""

import random
import threading
import time
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from config.config_cilent import (
    CRAWL_DELAY_MIN,
    CRAWL_DELAY_MAX,
    CRAWL_MAX_RETRIES,
    CRAWL_MAX_AGE_YEARS,
    USER_AGENTS,
)
from logging_config import get_logger

logger = get_logger("crawler")


# ── 도메인별 요청 rate 제한 (스레드 전체 공유) ────────────────────────────────
#
# 병렬 크롤에서 차단을 유발하는 건 '동시 연결 수'가 아니라 '단위시간당 요청 수'다.
# 세마포어는 동시성만 막을 뿐, random_delay 가 스레드마다 독립적으로 잠들기 때문에
# 스레드 수에 비례해 rate 가 올라간다. RateLimiter 는 한 도메인으로 가는 '요청 시작
# 간격'을 스레드 전체에 걸쳐 강제하므로, 워커를 몇 개로 늘려도 rate 는 직렬과 동일.
# → 동시성과 요청 rate 를 독립적으로 조절할 수 있게 된다.
class RateLimiter:
    def __init__(self, min_gap: float, max_gap: float):
        self._lock = threading.Lock()
        self._min = min_gap
        self._max = max_gap
        self._next_at = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_at - now)
            # 매 요청 간격에 지터를 유지해 고정 패턴 탐지를 피한다.
            gap = random.uniform(self._min, self._max)
            self._next_at = max(now, self._next_at) + gap
        if wait > 0:
            time.sleep(wait)  # 락 밖에서 잔다 → 대기 중 다른 스레드가 슬롯 계산 가능


_domain_limiters: dict[str, RateLimiter] = {}
_domain_lock = threading.Lock()


def rate_limit(url: str) -> None:
    """url 의 도메인 기준으로 요청 rate 를 제한한다(도메인마다 별도 리미터)."""
    domain = urlparse(url).netloc
    with _domain_lock:
        limiter = _domain_limiters.get(domain)
        if limiter is None:
            limiter = RateLimiter(CRAWL_DELAY_MIN, CRAWL_DELAY_MAX)
            _domain_limiters[domain] = limiter
    limiter.acquire()


def get_date_cutoff() -> datetime:
    """
    수집 대상 게시글의 날짜 하한선 반환.
    이 날짜보다 오래된 게시글은 크롤링에서 제외한다.
    (밈은 생명주기가 있어 옛날 글은 현재 쓰임새와 다를 수 있음)
    """
    return datetime.now(timezone.utc) - timedelta(days=CRAWL_MAX_AGE_YEARS * 365)


def merge_dedup_by_url(
    *post_lists: list[tuple[str, datetime | None]]
) -> list[tuple[str, datetime | None]]:
    """여러 정렬 기준으로 따로 수집한 (url, date) 목록을 URL 기준 중복 없이 합친다.

    정렬 하나만으로 검색하면 "아직 인기를 못 얻은 최신 글"이 계속 순위 밖으로
    밀리는 편향이 생긴다(인기순은 추천이 쌓일 시간이 필요해서 갓 올라온 글은
    못 낌). natepann/dcinside는 성격이 다른 정렬(예: 인기+최신)로 나눠 수집한 뒤
    이 함수로 합치는데, 같은 글이 두 정렬 모두에서 나올 수 있어 중복 제거가
    필요하다. 먼저 나온 정렬의 결과를 유지한다.
    """
    seen: set[str] = set()
    merged: list[tuple[str, datetime | None]] = []
    for posts in post_lists:
        for url, date in posts:
            if url not in seen:
                seen.add(url)
                merged.append((url, date))
    return merged


# ── 1. 랜덤 딜레이 ────────────────────────────────────────────────────────────

def random_delay():
    """
    요청과 요청 사이에 랜덤한 시간 동안 대기.

    왜 랜덤인가?
    - 고정 간격(예: 매 2초)이면 서버 로그에서 패턴이 잡혀 봇으로 탐지됨.
    - 사람의 클릭 간격은 불규칙하므로, 랜덤 딜레이가 더 자연스럽게 보임.
    """
    delay = random.uniform(CRAWL_DELAY_MIN, CRAWL_DELAY_MAX)
    time.sleep(delay)


# ── 2. 세션 생성 ──────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    """
    브라우저처럼 보이는 헤더가 세팅된 Session을 반환.

    Session을 쓰는 이유:
    - 매 요청마다 새로운 TCP 연결을 열지 않고 재사용 → 서버 부담 감소.
    - 쿠키가 자동으로 유지되어 로그인 세션 등을 처리할 수 있음.

    헤더 설명:
    - User-Agent   : 어떤 브라우저인지 알려주는 문자열. 없으면 'python-requests'로 잡혀 즉시 차단.
    - Accept       : 클라이언트가 받을 수 있는 콘텐츠 타입. 브라우저는 항상 이걸 보냄.
    - Accept-Language : 선호 언어. 한국 사이트에 한국어로 요청하는 것처럼 보이게 함.
    - Accept-Encoding : 압축 방식. gzip 지원을 알리면 전송량이 줄고 더 자연스럽게 보임.
    - Connection   : keep-alive = 연결을 끊지 말고 유지하라는 신호.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    return session


# ── 3. 차단 감지 ──────────────────────────────────────────────────────────────

# 서버가 차단할 때 HTML 본문에 자주 등장하는 키워드
_BLOCK_SIGNALS = [
    "captcha",
    "CAPTCHA",
    "로봇이 아닙니다",
    "자동화된 요청",
    "비정상적인 접근",
    "비정상 접근",
    "접근이 제한",
    "IP가 차단",
    "too many requests",
    "Too Many Requests",
    "Access Denied",
    "Forbidden",
]

def is_blocked(response: requests.Response) -> bool:
    """
    응답을 보고 서버가 실제로 차단했는지 판단.

    왜 필요한가?
    - 서버가 차단할 때 항상 429/403 에러를 돌려주지 않음.
    - 겉으로는 200 OK를 주면서 실제 내용 대신 CAPTCHA 페이지나
      로그인 페이지를 보내는 경우가 많음.
    - 이걸 감지 못하면 엉뚱한 HTML이 MongoDB에 저장됨.

    False positive 방지:
    - 일부 사이트(예: 나무위키)는 200 OK + 본문 내용이 있으면서도
      HTML 어딘가에 'captcha' 단어를 포함하는 경우가 있음.
    - 이를 막기 위해 키워드 신호는 응답 본문이 충분히 짧을 때만 적용.
      (실제 CAPTCHA 페이지는 내용이 적고, 정상 페이지는 내용이 많음)
    """
    # HTTP 상태 코드로 1차 확인
    if response.status_code in (403, 429, 503):
        return True

    text = response.text

    # 본문이 충분히 길면(3000자 이상) 키워드 신호는 false positive로 간주
    # 실제 차단 페이지는 안내 문구만 있어 짧고, 정상 페이지는 길다.
    if len(text) > 3000:
        return False

    return any(signal in text for signal in _BLOCK_SIGNALS)


# ── 4. 안전한 GET 요청 ────────────────────────────────────────────────────────

def safe_get(
    session: requests.Session,
    url: str,
    referer: str = None,
    timeout: int = 10,
) -> requests.Response | None:
    """
    딜레이 + 재시도 + 차단 감지가 포함된 GET 요청.

    매개변수:
    - session  : make_session()으로 만든 Session 객체
    - url      : 요청할 URL
    - referer  : Referer 헤더 값. 있으면 '이 페이지에서 링크를 타고 왔다'는 신호가 됨.
    - timeout  : 응답 대기 최대 시간 (초)

    Exponential backoff란?
    - 실패할 때마다 대기 시간을 2배씩 늘리는 전략.
    - 1회 실패 → 2초 대기, 2회 실패 → 4초 대기, 3회 실패 → 8초 대기.
    - 서버가 과부하 상태일 때 계속 두드리지 않고 여유를 주는 방식.

    반환값:
    - 성공 시 Response 객체
    - 모든 재시도 실패 또는 차단 감지 시 None
    """
    headers = {}
    if referer:
        headers["Referer"] = referer

    for attempt in range(1, CRAWL_MAX_RETRIES + 1):
        try:
            rate_limit(url)  # 스레드 전체에 걸쳐 이 도메인 요청 rate 를 직렬 수준으로 유지
            response = session.get(url, headers=headers, timeout=timeout)

            if is_blocked(response):
                logger.warning("차단 감지 — %s (시도 %d/%d)", url, attempt, CRAWL_MAX_RETRIES)
                return None

            return response

        except requests.exceptions.Timeout:
            logger.warning("타임아웃 — %s (시도 %d/%d)", url, attempt, CRAWL_MAX_RETRIES)
        except requests.exceptions.ConnectionError:
            logger.warning("연결 오류 — %s (시도 %d/%d)", url, attempt, CRAWL_MAX_RETRIES)
        except requests.exceptions.RequestException as e:
            logger.warning("요청 오류 — %s: %s (시도 %d/%d)", url, e, attempt, CRAWL_MAX_RETRIES)

        backoff = 2 ** attempt
        logger.info("%d초 후 재시도...", backoff)
        time.sleep(backoff)

    logger.error("최대 재시도 초과, 포기 — %s", url)
    return None

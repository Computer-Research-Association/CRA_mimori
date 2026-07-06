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
import time
import requests

from config.config_cilent import (
    CRAWL_DELAY_MIN,
    CRAWL_DELAY_MAX,
    CRAWL_MAX_RETRIES,
    USER_AGENTS,
)


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
            random_delay()
            response = session.get(url, headers=headers, timeout=timeout)

            if is_blocked(response):
                print(f"[base] 차단 감지 — {url} (시도 {attempt}/{CRAWL_MAX_RETRIES})")
                # 차단은 재시도해도 소용없을 가능성이 높으므로 바로 None 반환
                return None

            return response

        except requests.exceptions.Timeout:
            print(f"[base] 타임아웃 — {url} (시도 {attempt}/{CRAWL_MAX_RETRIES})")
        except requests.exceptions.ConnectionError:
            print(f"[base] 연결 오류 — {url} (시도 {attempt}/{CRAWL_MAX_RETRIES})")
        except requests.exceptions.RequestException as e:
            print(f"[base] 요청 오류 — {url}: {e} (시도 {attempt}/{CRAWL_MAX_RETRIES})")

        # Exponential backoff: 2^attempt 초 대기
        backoff = 2 ** attempt
        print(f"[base] {backoff}초 후 재시도...")
        time.sleep(backoff)

    print(f"[base] 최대 재시도 초과, 포기 — {url}")
    return None

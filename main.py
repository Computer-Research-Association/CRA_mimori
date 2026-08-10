import sys
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from logging_config import get_logger
from perf_log import stage, report_totals
from crawlers.tavily_crawler import crawl
from crawlers.duckduckgo_crawler import crawl_duckduckgo
from crawlers.youtube_crawler import crawl_youtube
from crawlers.namuwiki_crawler import crawl_namuwiki
from crawlers.natepann_crawler import crawl_natepann
from crawlers.dcinside_crawler import crawl_dcinside
from crawlers.todayhumor_crawler import crawl_todayhumor
from config.config_cilent import CRAWL_WORKERS, MIN_COMMUNITY_DOCS_FOR_TAVILY
from trend.trend_service import get_meme_trend, save_trend_score

logger = get_logger("main")

# 1단계: 항상 먼저 도는 커뮤니티 크롤러. 이 합계가 MIN_COMMUNITY_DOCS_FOR_TAVILY
# 미만인 키워드만 2단계(Tavily → 실패 시 DuckDuckGo)로 보완한다.
COMMUNITY_CRAWLERS = {
    "youtube":    crawl_youtube,
    "namuwiki":   crawl_namuwiki,
    "natepann":   crawl_natepann,
    "dcinside":   crawl_dcinside,
    "todayhumor": crawl_todayhumor,
}

# 요약표 출력 순서 고정용(judge_and_report). 실제로 호출됐는지와 무관하게 항상
# 이 순서로 표시하고, 호출 안 된 소스는 "미실행"으로 나온다.
CRAWLERS = {**COMMUNITY_CRAWLERS, "tavily": crawl, "duckduckgo": crawl_duckduckgo}

KEYWORDS_PATH = os.path.join(BASE_DIR, "crawlers", "Keywords.md")

# 병렬 크롤 중 에러 로그가 다른 작업 로그와 섞여도 한 줄은 온전히 찍히게 한다.
_PRINT_LOCK = threading.Lock()


def load_keywords():
    with open(KEYWORDS_PATH, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _crawl_one(keyword: str, name: str, crawler) -> tuple[int, str]:
    """한 (키워드, 소스) 크롤 작업. (문서 수, 상태)를 반환하며 예외를 던지지 않는다.

    '실패'와 '수집 0건'을 구분하기 위해 상태를 함께 돌려준다 — 병렬 로그에서는
    에러 print 가 다른 작업 사이에 파묻히므로, 요약표에서 사유를 볼 수 있어야 한다.
    """
    try:
        with stage("크롤", keyword=keyword, source=name):
            docs = crawler(keyword)
        return len(docs), "ok"
    except Exception as e:
        with _PRINT_LOCK:
            logger.error("[%s/%s] 크롤 실패: %s", keyword, name, e)
        return 0, f"실패({type(e).__name__})"


def _crawl_tavily_with_fallback(keyword: str) -> dict[str, tuple[int, str]]:
    """Tavily를 시도하고, 예외로 실패한 경우에만 DuckDuckGo로 한 번 더 보완한다.

    두 시도의 결과를 모두 남긴다 — Tavily가 실패했다는 사실 자체가 진단에 필요한
    정보라, DuckDuckGo 성공으로 덮어써서 감추지 않는다(요약표에 둘 다 나와야
    "Tavily가 왜 안 도나"를 나중에 로그로 추적할 수 있다).

    DuckDuckGo는 Tavily가 예외를 던졌을 때만 호출한다 — Tavily가 정상적으로
    0건을 반환한 경우(필터로 다 걸러짐, 최근 재크롤 스킵 등)는 실패가 아니므로
    폴백을 트리거하지 않는다.
    """
    updates: dict[str, tuple[int, str]] = {}
    count, status = _crawl_one(keyword, "tavily", crawl)
    updates["tavily"] = (count, status)

    if status.startswith("실패"):
        logger.info("[%s] Tavily 실패(%s) — DuckDuckGo 폴백 시도", keyword, status)
        updates["duckduckgo"] = _crawl_one(keyword, "duckduckgo", crawl_duckduckgo)

    return updates


def crawl_keyword(keyword: str) -> dict[str, tuple[int, str]]:
    """한 키워드에 대해 커뮤니티 우선 → 부족 시 Tavily/DuckDuckGo 보완 크롤을 수행한다.

    crawl_all()의 다중 키워드 flat-pool 최적화는 건드리지 않고, RAG 질의처럼
    키워드 하나만 그 자리에서 수집해야 하는 호출부(rag_main.py 등)를 위한
    별도 진입점이다. 우선순위 로직(_crawl_one/_crawl_tavily_with_fallback)은
    crawl_all()과 동일한 것을 그대로 재사용한다.

    반환: {source: (count, status)}
    """
    results: dict[str, tuple[int, str]] = {}
    with ThreadPoolExecutor(max_workers=len(COMMUNITY_CRAWLERS)) as executor:
        future_to_name = {
            executor.submit(_crawl_one, keyword, name, crawler): name
            for name, crawler in COMMUNITY_CRAWLERS.items()
        }
        for future in as_completed(future_to_name):
            results[future_to_name[future]] = future.result()

    if sum(count for count, _ in results.values()) < MIN_COMMUNITY_DOCS_FOR_TAVILY:
        results.update(_crawl_tavily_with_fallback(keyword))

    return results


def add_keyword_if_missing(keyword: str) -> bool:
    """Keywords.md에 없는 키워드면 한 줄 추가한다. 추가했으면 True, 이미 있었으면 False.

    온디맨드로 수집된 키워드를 다음 배치 크롤/트렌드 판정 대상에 편입시키기 위함
    (issue #69). load_keywords()와 동일하게 줄 단위 strip 기준으로 중복을 비교한다.
    """
    if keyword in load_keywords():
        return False
    with open(KEYWORDS_PATH, "a", encoding="utf-8") as f:
        f.write(f"{keyword}\n")
    return True


def crawl_all(keywords: list[str]) -> dict[str, dict[str, tuple[int, str]]]:
    """크롤을 2단계로 나눠 돈다.

    1단계: 커뮤니티 크롤러(COMMUNITY_CRAWLERS)를 (키워드 × 소스) 전체가 하나의 평평한
    풀에서 병렬로 돈다. 키워드마다 중첩 풀을 만들지 않아(head-of-line blocking 제거)
    스크래퍼의 요청 rate 는 crawlers/base.py 의 도메인별 RateLimiter 가 직렬 수준으로
    묶으므로, 워커 수를 늘려도 사이트별 rate 는 안전하게 유지된다.

    2단계: 1단계 합계가 MIN_COMMUNITY_DOCS_FOR_TAVILY 미만인 키워드만 Tavily로
    보완한다(그 키워드들끼리도 평평한 풀에서 병렬 — Tavily/DuckDuckGo는 API·쿼터
    기반이라 동시 요청에 관대함). Tavily가 예외로 실패하면 DuckDuckGo까지 보완한다.

    반환: {keyword: {source: (count, status)}}
    """
    community_tasks = [
        (kw, name, crawler)
        for kw in keywords
        for name, crawler in COMMUNITY_CRAWLERS.items()
    ]
    results: dict[str, dict[str, tuple[int, str]]] = defaultdict(dict)

    with stage("크롤:커뮤니티(벽시계)", 작업수=len(community_tasks), 워커=CRAWL_WORKERS):
        with ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as executor:
            future_to_task = {
                executor.submit(_crawl_one, kw, name, crawler): (kw, name)
                for kw, name, crawler in community_tasks
            }
            # as_completed + 개별 결과 저장: 한 작업이 죽어도 나머지 결과는 온전히 남는다
            # (executor.map 은 첫 예외에서 소비가 끊겨 뒤 작업 결과가 통째로 유실됨).
            for future in as_completed(future_to_task):
                kw, name = future_to_task[future]
                results[kw][name] = future.result()  # _crawl_one 은 예외를 삼키므로 안전

    needs_tavily = [
        kw for kw in keywords
        if sum(count for count, _ in results[kw].values()) < MIN_COMMUNITY_DOCS_FOR_TAVILY
    ]

    if needs_tavily:
        logger.info(
            "커뮤니티 수집 %d건 미만: %d개 키워드에 Tavily 보완 — %s",
            MIN_COMMUNITY_DOCS_FOR_TAVILY, len(needs_tavily), ", ".join(needs_tavily),
        )
        with stage("크롤:Tavily 보완(벽시계)", 작업수=len(needs_tavily)):
            with ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as executor:
                future_to_kw = {
                    executor.submit(_crawl_tavily_with_fallback, kw): kw
                    for kw in needs_tavily
                }
                for future in as_completed(future_to_kw):
                    kw = future_to_kw[future]
                    results[kw].update(future.result())

    return results


def judge_and_report(keyword: str, source_results: dict[str, tuple[int, str]]) -> None:
    """크롤 완료 후(순차) 트렌드 판정 + 저장 + 요약 출력.

    트렌드 API(네이버/카카오/pytrends)는 커뮤니티 스크래퍼보다 rate limit 이 빡세고,
    구글 캐시가 공유 파일이라 병렬로 돌리면 429·캐시 손상 위험이 크다. 그래서 크롤과
    분리해 이 단계는 키워드마다 '순차'로 판정한다(동시 호출 자체를 없앰).
    """
    total = sum(count for count, _ in source_results.values())

    trend = None
    trend_line = f"  {'트렌드':<12}: 판정 안 됨"
    try:
        with stage("트렌드 판정", keyword=keyword):
            trend = get_meme_trend(keyword)
        sources = ", ".join(trend["sources"]) or "없음"
        trend_line = f"  {'트렌드':<12}: {trend['status']} (z={trend['final_z']:.2f}, 소스: {sources})"
    except Exception as e:
        trend_line = f"  {'트렌드':<12}: 판정 실패 ({e})"

    if trend is not None:
        try:
            with stage("트렌드 저장", keyword=keyword):
                save_trend_score(trend)
        except Exception as e:
            trend_line += f"  [저장 실패: {e}]"

    lines = ["", "=" * 40, f"키워드: '{keyword}' 수집 완료", "=" * 40]
    for name in CRAWLERS:  # 출력은 CRAWLERS 정의 순서로 고정
        count, status = source_results.get(name, (0, "미실행"))
        cell = f"{count}개" if status == "ok" else f"{count}개 [{status}]"
        lines.append(f"  {name:<12}: {cell}")
    lines.append(f"  {'합계':<12}: {total}개")
    lines.append(trend_line)
    summary = "\n".join(lines)
    logger.info(summary)


if __name__ == "__main__":
    keywords = load_keywords()
    if not keywords:
        logger.warning("%s에 등록된 키워드가 없습니다.", KEYWORDS_PATH)
        sys.exit(1)

    # 1단계: 크롤(병렬, 도메인별 rate limit) → 2단계: 트렌드 판정/저장/출력(순차)
    results = crawl_all(keywords)
    for keyword in keywords:
        judge_and_report(keyword, results[keyword])

    report_totals("main.py 단계별 누적 소요 시간")

import sys
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from crawlers.tavily_crawler import crawl
from crawlers.youtube_crawler import crawl_youtube
from crawlers.namuwiki_crawler import crawl_namuwiki
from crawlers.natepann_crawler import crawl_natepann
from crawlers.dcinside_crawler import crawl_dcinside
from crawlers.todayhumor_crawler import crawl_todayhumor
from config.config_cilent import CRAWL_WORKERS
from trend.trend_service import get_meme_trend, save_trend_score

CRAWLERS = {
    "tavily":     crawl,
    "youtube":    crawl_youtube,
    "namuwiki":   crawl_namuwiki,
    "natepann":   crawl_natepann,
    "dcinside":   crawl_dcinside,
    "todayhumor": crawl_todayhumor,
}

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
        docs = crawler(keyword)
        return len(docs), "ok"
    except Exception as e:
        with _PRINT_LOCK:
            print(f"[{keyword}/{name}] 크롤 실패: {e}")
        return 0, f"실패({type(e).__name__})"


def crawl_all(keywords: list[str]) -> dict[str, dict[str, tuple[int, str]]]:
    """모든 (키워드 × 소스) 작업을 하나의 평평한 풀에서 병렬 크롤한다.

    키워드마다 중첩 풀을 만들지 않고(head-of-line blocking 제거) 전 작업을 한 풀에
    넣는다. 스크래퍼의 요청 rate 는 crawlers/base.py 의 도메인별 RateLimiter 가
    직렬 수준으로 묶으므로, 워커 수를 늘려도 사이트별 rate 는 안전하게 유지된다.

    반환: {keyword: {source: (count, status)}}
    """
    tasks = [
        (kw, name, crawler)
        for kw in keywords
        for name, crawler in CRAWLERS.items()
    ]
    results: dict[str, dict[str, tuple[int, str]]] = defaultdict(dict)

    with ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as executor:
        future_to_task = {
            executor.submit(_crawl_one, kw, name, crawler): (kw, name)
            for kw, name, crawler in tasks
        }
        # as_completed + 개별 결과 저장: 한 작업이 죽어도 나머지 결과는 온전히 남는다
        # (executor.map 은 첫 예외에서 소비가 끊겨 뒤 작업 결과가 통째로 유실됨).
        for future in as_completed(future_to_task):
            kw, name = future_to_task[future]
            results[kw][name] = future.result()  # _crawl_one 은 예외를 삼키므로 안전

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
        trend = get_meme_trend(keyword)
        sources = ", ".join(trend["sources"]) or "없음"
        trend_line = f"  {'트렌드':<12}: {trend['status']} (z={trend['final_z']:.2f}, 소스: {sources})"
    except Exception as e:
        trend_line = f"  {'트렌드':<12}: 판정 실패 ({e})"

    if trend is not None:
        try:
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
    print("\n".join(lines))


if __name__ == "__main__":
    keywords = load_keywords()
    if not keywords:
        print(f"{KEYWORDS_PATH}에 등록된 키워드가 없습니다.")
        sys.exit(1)

    # 1단계: 크롤(병렬, 도메인별 rate limit) → 2단계: 트렌드 판정/저장/출력(순차)
    results = crawl_all(keywords)
    for keyword in keywords:
        judge_and_report(keyword, results[keyword])

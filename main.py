import asyncio
import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from crawlers.tavily_crawler import crawl
from crawlers.youtube_crawler import crawl_youtube
from crawlers.namuwiki_crawler import crawl_namuwiki
from crawlers.natepann_crawler import crawl_natepann
from crawlers.dcinside_crawler import crawl_dcinside
from config.config_cilent import KEYWORD_WORKERS, SOURCE_CONCURRENCY
from trend.trend_service import get_meme_trend, save_trend_score

CRAWLERS = {
    "tavily":    crawl,
    "youtube":   crawl_youtube,
    "namuwiki":  crawl_namuwiki,
    "natepann":  crawl_natepann,
    "dcinside":  crawl_dcinside,
}

KEYWORDS_PATH = os.path.join(BASE_DIR, "crawlers", "Keywords.md")

# 소스별 동시 크롤 상한 세마포어.
# 키워드/소스를 병렬로 돌려도 같은 사이트에 동시에 진입하는 크롤러 수를 이 값으로
# 묶어 차단을 막는다. 모든 키워드 스레드가 이 세마포어를 공유하므로, 예컨대
# dcinside=2 면 전체를 통틀어 dcinside 크롤은 항상 최대 2개만 동시에 돈다.
_SOURCE_SEMAPHORES = {
    name: threading.Semaphore(SOURCE_CONCURRENCY.get(name, 1)) for name in CRAWLERS
}

# 여러 키워드가 동시에 결과를 출력하면 줄이 뒤섞이므로, 키워드별 요약 블록은
# 이 락으로 원자적으로 찍는다(크롤러 내부 진행 로그는 그대로 흘려보냄).
_PRINT_LOCK = threading.Lock()


def load_keywords():
    with open(KEYWORDS_PATH, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _run_crawler(name: str, crawler, keyword: str) -> int:
    """소스 세마포어를 잡고 크롤러를 실행해 수집 문서 수를 반환."""
    with _SOURCE_SEMAPHORES[name]:
        return len(crawler(keyword))


def crawl_all_sources(keyword: str) -> dict[str, int]:
    """
    한 키워드에 대해 5개 소스를 스레드로 동시에 크롤링한다(소스별 세마포어 적용).

    소스별로 접속하는 서버가 전부 달라 소스 병렬화 자체는 차단 위험이 낮지만,
    키워드까지 병렬로 돌리면(process_keyword) 여러 키워드 스레드가 같은 사이트
    (예: dcinside)를 동시에 두드릴 수 있다. 그걸 _SOURCE_SEMAPHORES 로 소스별
    동시 진입 수를 묶어 막는다.

    PyMongo MongoClient는 스레드 안전하므로 크롤러들이 같은 싱글톤 컬렉션을
    공유해도 문제없다. 소스별 예외는 개별로 흡수해 한 소스가 실패해도 나머지
    결과는 유지한다.
    """
    results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=len(CRAWLERS)) as executor:
        future_to_name = {
            executor.submit(_run_crawler, name, crawler, keyword): name
            for name, crawler in CRAWLERS.items()
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as e:
                with _PRINT_LOCK:
                    print(f"[{name}] 오류: {e}")
                results[name] = 0
    return results


def process_keyword(keyword: str) -> None:
    """한 키워드의 크롤링 + 트렌드 판정/저장 + 요약 출력을 수행한다.

    키워드 단위로 병렬 실행되므로, 이 함수 하나가 워커 스레드 1개의 작업량이다.
    """
    results = crawl_all_sources(keyword)
    total = sum(results.values())

    # 크롤링과 별개로 트렌드 판정 → trend_scores 저장.
    # 판정과 저장을 분리한다: DB 장애로 저장이 실패해도 (네트워크 비용 들여) 이미
    # 계산한 판정 결과는 버리지 않고 출력한다. 각 단계를 개별로 감싸 어느 쪽이
    # 실패해도 크롤 루프는 계속된다(보조 지표 실패는 get_meme_trend 내부에서 흡수됨).
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

    # 요약 블록은 락으로 묶어 다른 키워드 출력과 섞이지 않게 한 번에 찍는다.
    lines = [
        "",
        "=" * 40,
        f"키워드: '{keyword}' 수집 완료",
        "=" * 40,
    ]
    # 병렬 수집이라 results 완료 순서는 뒤섞이므로, 출력은 CRAWLERS 정의 순서로 고정
    lines += [f"  {name:<12}: {results.get(name, 0)}개" for name in CRAWLERS]
    lines.append(f"  {'합계':<12}: {total}개")
    lines.append(trend_line)
    with _PRINT_LOCK:
        print("\n".join(lines))


if __name__ == "__main__":
    keywords = load_keywords()
    if not keywords:
        print(f"{KEYWORDS_PATH}에 등록된 키워드가 없습니다.")
        sys.exit(1)

    # 키워드 병렬(바깥) × 소스 병렬(안쪽 crawl_all_sources) = 2중 병렬.
    # 같은 사이트 동시 요청은 _SOURCE_SEMAPHORES 가 소스별 상한으로 막아준다.
    with ThreadPoolExecutor(max_workers=KEYWORD_WORKERS) as executor:
        # list()로 소비해 각 작업의 예외가 여기서 드러나게 한다(map은 지연 평가).
        list(executor.map(process_keyword, keywords))

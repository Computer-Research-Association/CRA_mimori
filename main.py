import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawlers.tavily_crawler import crawl
from crawlers.youtube_crawler import crawl_youtube
from crawlers.namuwiki_crawler import crawl_namuwiki
from crawlers.natepann_crawler import crawl_natepann
from crawlers.dcinside_crawler import crawl_dcinside
from trend.trend_service import get_meme_trend

CRAWLERS = {
    "tavily":    crawl,
    "youtube":   crawl_youtube,
    "namuwiki":  crawl_namuwiki,
    "natepann":  crawl_natepann,
    "dcinside":  crawl_dcinside,
}


async def _run_crawler(name: str, fn, keyword: str) -> tuple[str, list]:
    try:
        docs = await asyncio.to_thread(fn, keyword)
        return name, docs
    except Exception as e:
        print(f"[{name}] 오류: {e}")
        return name, []


async def _run_trend(keyword: str) -> dict | None:
    try:
        return await asyncio.to_thread(get_meme_trend, keyword)
    except Exception as e:
        print(f"[trend] 오류: {e}")
        return None


async def pipeline(keyword: str) -> dict:
    """
    크롤링 5개 + 트렌드 분석을 병렬 실행하고 결과를 합쳐서 반환한다.

    반환:
        {
            "keyword": str,
            "crawl": {"tavily": int, ...},
            "total_docs": int,
            "trend": {"z_score": float, "status": str, "ratios": [...]} | None,
        }
    """
    crawler_tasks = [
        _run_crawler(name, fn, keyword)
        for name, fn in CRAWLERS.items()
    ]

    *crawler_results, trend = await asyncio.gather(
        *crawler_tasks,
        _run_trend(keyword),
    )

    crawl_counts = {name: len(docs) for name, docs in crawler_results}

    return {
        "keyword": keyword,
        "crawl": crawl_counts,
        "total_docs": sum(crawl_counts.values()),
        "trend": trend,
    }


def _print_result(result: dict) -> None:
    keyword = result["keyword"]

    print()
    print("=" * 40)
    print(f"키워드: '{keyword}' 수집 완료")
    print("=" * 40)
    for name, count in result["crawl"].items():
        print(f"  {name:<12}: {count}개")
    print(f"  {'합계':<12}: {result['total_docs']}개")

    print()
    print("=" * 40)
    trend = result["trend"]
    if trend:
        print(f"  z_score : {trend['z_score']:.4f}")
        print(f"  상태    : {trend['status']}")
        print(f"  데이터  : {len(trend['ratios'])}건")
    else:
        print("  트렌드 분석 실패")


if __name__ == "__main__":
    keyword = input("검색할 밈/신조어 입력: ").strip()
    if not keyword:
        print("키워드를 입력하세요.")
        sys.exit(1)

    result = asyncio.run(pipeline(keyword))
    _print_result(result)

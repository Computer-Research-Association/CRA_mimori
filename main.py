import asyncio
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from crawlers.tavily_crawler import crawl
from crawlers.youtube_crawler import crawl_youtube
from crawlers.namuwiki_crawler import crawl_namuwiki
from crawlers.natepann_crawler import crawl_natepann
from crawlers.dcinside_crawler import crawl_dcinside
from trend.trend_service import get_meme_trend, save_trend_score

CRAWLERS = {
    "tavily":    crawl,
    "youtube":   crawl_youtube,
    "namuwiki":  crawl_namuwiki,
    "natepann":  crawl_natepann,
    "dcinside":  crawl_dcinside,
}

KEYWORDS_PATH = os.path.join(BASE_DIR, "crawlers", "Keywords.md")


def load_keywords():
    with open(KEYWORDS_PATH, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


if __name__ == "__main__":
    keywords = load_keywords()
    if not keywords:
        print(f"{KEYWORDS_PATH}에 등록된 키워드가 없습니다.")
        sys.exit(1)

    for keyword in keywords:
        total = 0
        results = {}

        for name, crawler in CRAWLERS.items():
            try:
                docs = crawler(keyword)
                results[name] = len(docs)
                total += len(docs)
            except Exception as e:
                print(f"[{name}] 오류: {e}")
                results[name] = 0

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

        print()
        print("=" * 40)
        print(f"키워드: '{keyword}' 수집 완료")
        print("=" * 40)
        for name, count in results.items():
            print(f"  {name:<12}: {count}개")
        print(f"  {'합계':<12}: {total}개")
        print(trend_line)

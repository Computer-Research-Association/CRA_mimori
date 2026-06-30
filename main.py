import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawlers.tavily_crawler import crawl
from crawlers.youtube_crawler import crawl_youtube
from crawlers.namuwiki_crawler import crawl_namuwiki
from crawlers.natepann_crawler import crawl_natepann
from crawlers.dcinside_crawler import crawl_dcinside

CRAWLERS = {
    "tavily":     crawl,
    "youtube":    crawl_youtube,
    "namuwiki":   crawl_namuwiki,
    "natepann":   crawl_natepann,
    "dcinside":   crawl_dcinside,
}

if __name__ == "__main__":
    keyword = input("검색할 밈/신조어 입력: ").strip()
    if not keyword:
        print("키워드를 입력하세요.")
        sys.exit(1)

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

    print()
    print("=" * 40)
    print(f"키워드: '{keyword}' 수집 완료")
    print("=" * 40)
    for name, count in results.items():
        print(f"  {name:<12}: {count}개")
    print(f"  {'합계':<12}: {total}개")

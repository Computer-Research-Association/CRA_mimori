import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

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

        print()
        print("=" * 40)
        print(f"키워드: '{keyword}' 수집 완료")
        print("=" * 40)
        for name, count in results.items():
            print(f"  {name:<12}: {count}개")
        print(f"  {'합계':<12}: {total}개")

"""실제 크롤러 5개를 지정한 키워드로 한 번씩 돌려본다. 로컬 Mongo(개발용, EC2와 분리됨)에 저장됨.

사용법: uv run python scripts/live_crawl_test.py <키워드>
        (키워드 생략 시 기본값 '오운완', 프로젝트 루트에서 실행할 것)
반드시 MONGODB_URI=mongodb://localhost:27017 을 앞에 붙여서 실행할 것
(.env의 기본값은 도커 내부 호스트명이라 호스트에서 직접 실행하면 연결이 실패함).
"""
import sys
sys.path.insert(0, ".")

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "오운완"

results = {}

print("=== dcinside ===")
try:
    from crawlers.dcinside_crawler import crawl_dcinside
    docs = crawl_dcinside(KEYWORD)
    results["dcinside"] = len(docs)
except Exception as e:
    print(f"실패: {e}")
    results["dcinside"] = f"실패: {e}"

print("\n=== natepann ===")
try:
    from crawlers.natepann_crawler import crawl_natepann
    docs = crawl_natepann(KEYWORD)
    results["natepann"] = len(docs)
except Exception as e:
    print(f"실패: {e}")
    results["natepann"] = f"실패: {e}"

print("\n=== tavily ===")
try:
    from crawlers.tavily_crawler import crawl
    docs = crawl(KEYWORD)
    results["tavily"] = len(docs)
except Exception as e:
    print(f"실패: {e}")
    results["tavily"] = f"실패: {e}"

print("\n=== todayhumor ===")
try:
    from crawlers.todayhumor_crawler import crawl_todayhumor
    docs = crawl_todayhumor(KEYWORD)
    results["todayhumor"] = len(docs)
except Exception as e:
    print(f"실패: {e}")
    results["todayhumor"] = f"실패: {e}"

print("\n=== namuwiki ===")
try:
    from crawlers.namuwiki_crawler import crawl_namuwiki
    docs = crawl_namuwiki(KEYWORD)
    results["namuwiki"] = len(docs)
except Exception as e:
    print(f"실패: {e}")
    results["namuwiki"] = f"실패: {e}"

print("\n\n=== 요약 ===")
for src, n in results.items():
    print(f"  {src}: {n}")

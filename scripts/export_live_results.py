"""이번 실행에서 새로 크롤링된 문서를 원본/처리결과 두 파일로 저장한다.

사용법: uv run python scripts/export_live_results.py <키워드> [최근 N분]
        (둘 다 생략 시 키워드 '오운완', 45분. 프로젝트 루트에서 실행할 것)
MONGODB_URI=mongodb://localhost:27017 을 앞에 붙여서 실행할 것.
"""
import json
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from DB.mongo_client import get_collection
from preprocessing.pipeline import process_one

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "오운완"
MINUTES = int(sys.argv[2]) if len(sys.argv) > 2 else 45

coll = get_collection()
cutoff = datetime.now(timezone.utc) - timedelta(minutes=MINUTES)

docs = list(coll.find({"keyword": KEYWORD, "crawled_at": {"$gte": cutoff}}))
print(f"'{KEYWORD}' 최근 {MINUTES}분 내 신규 문서 {len(docs)}건 발견")

# 1. 원본 데이터 저장
with open("data_test/live_test_raw.jsonl", "w", encoding="utf-8") as f:
    for d in docs:
        f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
print("원본 저장: data_test/live_test_raw.jsonl")

# 2. 전처리+청킹 결과 저장
processed = []
for d in docs:
    clean_content, chunks = process_one(d)
    processed.append({
        "_id": str(d.get("_id")),
        "source": d.get("source"),
        "title": d.get("title"),
        "url": d.get("url"),
        "keyword": d.get("keyword"),
        "raw_content_len": len(d.get("content", "")),
        "clean_content": clean_content,
        "clean_content_len": len(clean_content),
        "chunk_count": len(chunks),
        "chunks": chunks,
    })

with open("data_test/live_test_processed.jsonl", "w", encoding="utf-8") as f:
    for p in processed:
        f.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")
print("처리결과 저장: data_test/live_test_processed.jsonl")

# 3. 요약 출력
print("\n=== 요약 ===")
for p in processed:
    print(f"[{p['source']}] {p['title'][:40]!r}")
    print(f"  원문 {p['raw_content_len']}자 -> 정제 {p['clean_content_len']}자 -> 청크 {p['chunk_count']}개")
    for c in p["chunks"]:
        print(f"    [{c.get('content_type')}] is_relevant={c.get('is_relevant')} len={len(c['text'])}")

"""
dashboard/export.py
Mongo에 수집된 키워드별 통계를 집계하고, 이미 임베딩된 키워드는 기존
analysis/pipeline.py를 재사용해 LLM 분석까지 더해 dashboard/public/data.json
스냅샷으로 저장한다.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from analysis.pipeline import analyze, build_prompt, fetch_keyword_chunks, list_analyzable_keywords
from DB.mongo_client import get_collection


def collect_keyword_stats() -> list[dict]:
    """memes 컬렉션 전체를 훑어 키워드별 총 개수, source별 분포, is_embedded
    여부를 집계. keyword 오름차순 정렬된 리스트로 반환.

    is_embedded는 analysis.pipeline.list_analyzable_keywords()와 동일한
    기준(해당 키워드에 is_embedded=True인 문서가 하나라도 있는지)을 그대로 쓴다."""
    collection = get_collection()
    analyzable = set(list_analyzable_keywords())

    counts: dict[str, dict] = {}
    for doc in collection.find({}, {"keyword": 1, "source": 1}):
        keyword = doc["keyword"]
        entry = counts.setdefault(
            keyword,
            {"keyword": keyword, "total_count": 0, "by_source": defaultdict(int)},
        )
        entry["total_count"] += 1
        entry["by_source"][doc.get("source", "unknown")] += 1

    results = []
    for entry in counts.values():
        entry["by_source"] = dict(entry["by_source"])
        entry["is_embedded"] = entry["keyword"] in analyzable
        results.append(entry)
    results.sort(key=lambda e: e["keyword"])
    return results


def build_snapshot() -> dict:
    """collect_keyword_stats() 결과에 임베딩된 키워드의 LLM 분석을 더해
    data.json에 쓸 전체 스냅샷 dict를 만든다. 특정 키워드 분석이 실패해도
    나머지는 계속 진행한다."""
    keyword_stats = collect_keyword_stats()

    for entry in keyword_stats:
        entry["analysis"] = None
        if not entry["is_embedded"]:
            continue
        try:
            chunks = fetch_keyword_chunks(entry["keyword"])
            prompt = build_prompt(entry["keyword"], chunks)
            entry["analysis"] = analyze(prompt)
        except Exception as exc:
            print(f"[경고] '{entry['keyword']}' 분석 실패, 건너뜀: {exc}")

    summary = {
        "total_keywords": len(keyword_stats),
        "total_docs": sum(e["total_count"] for e in keyword_stats),
        "analyzed_keywords": sum(1 for e in keyword_stats if e["analysis"] is not None),
    }

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "summary": summary,
        "keywords": keyword_stats,
    }


_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "public", "data.json")


def write_snapshot(snapshot: dict, path: str = _OUTPUT_PATH) -> None:
    """스냅샷 dict를 UTF-8 JSON 파일로 저장한다 (한글 그대로, ensure_ascii=False).
    대상 폴더가 없으면 만든다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

"""
quick_partial.py
발표 등 급할 때: 새 NIM 호출 없이, 이미 judge_cache.json에 있는 청크만으로
지금 당장 낼 수 있는 부분 결과를 계산한다. 캐시가 없는 쿼리는 건너뛴다.
전체 run_compare.py는 백그라운드에서 계속 돌아가며 나머지를 채운다.

실행: python -m eval.quick_partial
"""

import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random

from analysis.pipeline import list_analyzable_keywords
from embedding.encoder import encode_batch, unload_model
from eval.judge import Judge
from eval.questions import build_eval_set, load_keywords
from eval.retrievers import METHODS, retrieve
from eval.run_compare import K_VALUES, _print_summary, _summarize, _write_outputs
from eval import metrics


def main() -> None:
    all_keywords = load_keywords()
    embedded = set(list_analyzable_keywords())
    usable = [k for k in all_keywords if k in embedded]
    queries = build_eval_set(usable)
    print(f"[평가셋] 키워드 {len(usable)}개 × 템플릿 → 쿼리 {len(queries)}개 (전체 기준)")

    print("[인코딩] 질문 임베딩 생성 중...")
    dense_vecs, sparse_weights = encode_batch([q.question for q in queries])
    unload_model()

    judge = Judge(model="deepseek-ai/deepseek-v4-flash")
    retrieve("dense", queries[0].keyword, dense_vecs[0], sparse_weights[0])  # 워밍업

    per_query_rows: list[dict] = []
    rels_map: dict[tuple[str, str], list[int]] = {}
    pool_map: dict[str, list[int]] = {}
    ids_map: dict[tuple[str, str], list[str]] = {}
    latency_acc: dict[str, list[float]] = {m: [] for m in METHODS}
    T = metrics.RELEVANT_THRESHOLD

    included, skipped_uncached = 0, 0

    for qi, query in enumerate(queries):
        dense_vec = dense_vecs[qi]
        sparse = sparse_weights[qi]
        order = list(METHODS)
        random.Random(qi).shuffle(order)
        results = {m: retrieve(m, query.keyword, dense_vec, sparse) for m in order}

        pool: dict[str, str] = {}
        for res in results.values():
            for c in res.chunks:
                pool.setdefault(c.chunk_id, c.text)

        # 캐시에 없으면 API 호출 없이 None으로 표시 -> 이 쿼리는 통째로 스킵(부분 풀은 nDCG 왜곡됨)
        cache_hits = {}
        for cid in pool:
            key = judge._cache_key(query.id, cid)
            hit = judge.cache.get(key)
            cache_hits[cid] = hit["score"] if hit is not None else None
        if any(v is None for v in cache_hits.values()):
            skipped_uncached += 1
            continue
        included += 1

        pool_rels = [cache_hits[cid] for cid in pool]
        rel_by_chunk = dict(zip(pool.keys(), pool_rels))
        pool_map[query.id] = pool_rels

        for m in METHODS:
            res = results[m]
            rels = [rel_by_chunk[c.chunk_id] for c in res.chunks]
            rels_map[(query.id, m)] = rels
            ids_map[(query.id, m)] = [c.chunk_id for c in res.chunks]
            latency_acc[m].append(res.latency_ms)

            row = {
                "keyword": query.keyword, "query_id": query.id, "question": query.question,
                "method": m, "latency_ms": round(res.latency_ms, 2),
                "mrr": round(metrics.mrr(rels, T), 4),
            }
            for k in K_VALUES:
                row[f"p@{k}"] = round(metrics.precision_at_k(rels, k, T), 4)
                row[f"avgrel@{k}"] = round(metrics.mean_relevance_at_k(rels, k), 4)
                row[f"ndcg@{k}"] = round(metrics.ndcg_at_k(rels, pool_rels, k), 4)
            per_query_rows.append(row)

    print(f"\n[부분 결과] 캐시로 완결된 쿼리 {included}/{len(queries)}개 "
          f"(캐시 부족으로 스킵 {skipped_uncached}개)")

    if included == 0:
        print("[중단] 캐시로 완결된 쿼리가 하나도 없습니다.")
        return

    included_queries = [q for q in queries if q.id in pool_map]
    summary = _summarize(rels_map, pool_map, ids_map, latency_acc, included_queries)
    summary["judge"] = {"parse_failures": 0, "api_failures": 0, "total_scored": 0}
    summary["partial"] = True
    summary["n_skipped_uncached"] = skipped_uncached

    _print_summary(summary, [])

    # 전체 결과와 안 섞이게 별도 파일로 저장
    orig_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    import eval.run_compare as rc
    rc._RESULTS_DIR = orig_dir
    for row in per_query_rows:
        row["_partial"] = True

    import json, csv
    csv_path = os.path.join(orig_dir, "per_query_PARTIAL.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_query_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_query_rows)
    summary_path = os.path.join(orig_dir, "summary_PARTIAL.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n[저장] {csv_path}")
    print(f"[저장] {summary_path}")


if __name__ == "__main__":
    main()

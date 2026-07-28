"""
run_compare.py
Dense / Sparse / Hybrid 검색 방식 비교 평가의 전체 흐름을 실행한다.

흐름:
  1) Keywords.md 키워드 × 질문 템플릿으로 평가셋 생성
  2) DB(Qdrant/Mongo)에 임베딩된 키워드만 남기고 필터
  3) 모든 질문을 BGE-M3로 한 번에 인코딩 후 모델 언로드
  4) 쿼리마다 세 방식으로 검색
  5) 쿼리별 union 청크를 NVIDIA로 관련도 채점(캐시)
  6) 방식별 Precision@k / nDCG@k / MRR / latency, 방식 간 Jaccard 겹침 계산
  7) results/ 에 CSV·JSON 저장하고 요약 표를 출력

실행:
    python -m eval.run_compare
    python eval/run_compare.py

필요 환경: Qdrant 접속(QDRANT_HOST/PORT), Mongo 접속, NIM_KEY(.env).
"""

import csv
import json
import os
import sys

# torch/네이티브 라이브러리 OpenMP 충돌 방지 (import torch 이전에 설정). rag_main.py와 동일.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from itertools import combinations

from analysis.pipeline import list_analyzable_keywords
from config.config_cilent import RAG_TOP_K
from embedding.encoder import encode_batch, unload_model
from eval import metrics
from eval.judge import Judge
from eval.questions import build_eval_set, load_keywords
from eval.retrievers import METHODS, retrieve

# 지표를 계산할 k 값들 (RAG_TOP_K 이하)
K_VALUES = sorted({3, RAG_TOP_K})

_RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def _filter_to_embedded(keywords: list[str]) -> tuple[list[str], list[str]]:
    """Keywords.md 키워드 중 DB에 임베딩된 것만 남긴다. (사용, 스킵) 튜플 반환."""
    embedded = set(list_analyzable_keywords())
    usable = [k for k in keywords if k in embedded]
    skipped = [k for k in keywords if k not in embedded]
    return usable, skipped


def main() -> None:
    os.makedirs(_RESULTS_DIR, exist_ok=True)

    all_keywords = load_keywords()
    usable, skipped = _filter_to_embedded(all_keywords)
    if skipped:
        print(f"[스킵] DB에 임베딩 안 된 키워드 {len(skipped)}개: {', '.join(skipped)}")
    if not usable:
        print("[중단] 평가 가능한(임베딩된) 키워드가 하나도 없습니다.")
        sys.exit(1)

    queries = build_eval_set(usable)
    print(f"[평가셋] 키워드 {len(usable)}개 × 템플릿 → 쿼리 {len(queries)}개")

    # 3) 질문 전부 한 번에 인코딩 (BGE-M3 한 번 로드) 후 언로드
    print("[인코딩] 질문 임베딩 생성 중...")
    dense_vecs, sparse_weights = encode_batch([q.question for q in queries])
    unload_model()

    judge = Judge()

    # latency 왜곡 방지: 첫 검색이 Qdrant 연결 셋업 비용을 뒤집어쓰지 않도록
    # 측정 밖에서 워밍업 1회 (결과는 버림)
    retrieve("dense", queries[0].keyword, dense_vecs[0], sparse_weights[0])

    per_query_rows: list[dict] = []          # CSV 한 줄 = (쿼리, 방식) 지표
    method_metric_acc: dict[str, dict] = {   # 방식별 지표 누적
        m: {"latency_ms": [], "mrr": [], **{f"p@{k}": [] for k in K_VALUES},
            **{f"avgrel@{k}": [] for k in K_VALUES},
            **{f"ndcg@{k}": [] for k in K_VALUES}}
        for m in METHODS
    }
    jaccard_acc: dict[str, list[float]] = {f"{a}-{b}": [] for a, b in combinations(METHODS, 2)}

    for qi, query in enumerate(queries):
        dense_vec = dense_vecs[qi]
        sparse = sparse_weights[qi]

        # 4) 세 방식 검색
        results = {m: retrieve(m, query.keyword, dense_vec, sparse) for m in METHODS}

        # 5) union 청크 채점 (방식 무관, 청크당 1회)
        pool: dict[str, str] = {}  # chunk_id -> text
        for res in results.values():
            for c in res.chunks:
                pool.setdefault(c.chunk_id, c.text)
        pool_rels = [
            judge.score(query.id, query.question, cid, text) for cid, text in pool.items()
        ]
        rel_by_chunk = dict(zip(pool.keys(), pool_rels))

        # 6) 방식별 지표
        for m in METHODS:
            res = results[m]
            rels = [rel_by_chunk[c.chunk_id] for c in res.chunks]
            row = {
                "keyword": query.keyword,
                "query_id": query.id,
                "question": query.question,
                "method": m,
                "latency_ms": round(res.latency_ms, 2),
                "mrr": round(metrics.mrr(rels), 4),
            }
            method_metric_acc[m]["latency_ms"].append(res.latency_ms)
            method_metric_acc[m]["mrr"].append(metrics.mrr(rels))
            for k in K_VALUES:
                p = metrics.precision_at_k(rels, k)
                a = metrics.mean_relevance_at_k(rels, k)
                n = metrics.ndcg_at_k(rels, pool_rels, k)
                row[f"p@{k}"] = round(p, 4)
                row[f"avgrel@{k}"] = round(a, 4)
                row[f"ndcg@{k}"] = round(n, 4)
                method_metric_acc[m][f"p@{k}"].append(p)
                method_metric_acc[m][f"avgrel@{k}"].append(a)
                method_metric_acc[m][f"ndcg@{k}"].append(n)
            per_query_rows.append(row)

        # 방식 간 결과 겹침
        ids = {m: [c.chunk_id for c in results[m].chunks] for m in METHODS}
        for a, b in combinations(METHODS, 2):
            jaccard_acc[f"{a}-{b}"].append(metrics.jaccard(ids[a], ids[b]))

        print(f"  [{qi + 1}/{len(queries)}] {query.question}  (판정 청크 {len(pool)}개)")

    # 7) 집계 + 저장 + 출력
    summary = _summarize(method_metric_acc, jaccard_acc)
    _write_outputs(per_query_rows, summary)
    _print_summary(summary, skipped)


def _summarize(method_metric_acc: dict, jaccard_acc: dict) -> dict:
    methods_summary = {}
    for m, acc in method_metric_acc.items():
        methods_summary[m] = {name: round(metrics.mean(vals), 4) for name, vals in acc.items()}
    overlap = {pair: round(metrics.mean(vals), 4) for pair, vals in jaccard_acc.items()}
    return {"methods": methods_summary, "overlap": overlap, "k_values": K_VALUES}


def _write_outputs(per_query_rows: list[dict], summary: dict) -> None:
    csv_path = os.path.join(_RESULTS_DIR, "per_query.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_query_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_query_rows)

    summary_path = os.path.join(_RESULTS_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n[저장] {csv_path}")
    print(f"[저장] {summary_path}")


def _print_summary(summary: dict, skipped: list[str]) -> None:
    k_values = summary["k_values"]
    cols = (["method"] + [f"p@{k}" for k in k_values] + [f"avgrel@{k}" for k in k_values]
            + [f"ndcg@{k}" for k in k_values] + ["mrr", "latency_ms"])
    widths = {c: max(len(c), 10) for c in cols}

    print("\n" + "=" * 60)
    print("검색 방식 비교 (평균)")
    print("=" * 60)
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("-" * len(header))
    for m, vals in summary["methods"].items():
        cells = [m.ljust(widths["method"])]
        for c in cols[1:]:
            cells.append(f"{vals[c]:.4f}".ljust(widths[c]) if c != "latency_ms"
                         else f"{vals[c]:.1f}".ljust(widths[c]))
        print("  ".join(cells))

    print("\n방식 간 결과 겹침 (Jaccard, 높을수록 비슷):")
    for pair, val in summary["overlap"].items():
        print(f"  {pair}: {val:.3f}")


if __name__ == "__main__":
    main()
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
import random
import statistics
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

    # 원자료(rels)를 그대로 보관해 두면 threshold/gain을 바꿔도 재검색 없이 다시 계산할 수 있다.
    per_query_rows: list[dict] = []                    # CSV 한 줄 = (쿼리, 방식) 대표 지표
    rels_map: dict[tuple[str, str], list[int]] = {}    # (query_id, method) -> 순위별 관련도
    pool_map: dict[str, list[int]] = {}                # query_id -> union 풀 관련도(IDCG 기준)
    ids_map: dict[tuple[str, str], list[str]] = {}     # (query_id, method) -> chunk id 순위
    latency_acc: dict[str, list[float]] = {m: [] for m in METHODS}
    T = metrics.RELEVANT_THRESHOLD                      # 대표 지표용 기본 임계값

    for qi, query in enumerate(queries):
        dense_vec = dense_vecs[qi]
        sparse = sparse_weights[qi]

        # 4) 세 방식 검색. latency 편향 방지: 고정 순서면 뒤 방식이 앞 방식이 데운
        #    Qdrant 세그먼트/OS 캐시 이득을 보므로, 쿼리마다 방식 순서를 셔플한다.
        order = list(METHODS)
        random.Random(qi).shuffle(order)
        results = {m: retrieve(m, query.keyword, dense_vec, sparse) for m in order}

        # 5) union 청크 채점 (방식 무관, 청크당 1회)
        pool: dict[str, str] = {}  # chunk_id -> text
        for res in results.values():
            for c in res.chunks:
                pool.setdefault(c.chunk_id, c.text)
        pool_rels = [
            judge.score(query.id, query.question, cid, text) for cid, text in pool.items()
        ]
        rel_by_chunk = dict(zip(pool.keys(), pool_rels))
        pool_map[query.id] = pool_rels

        # 6) 방식별 원자료 저장 + 대표 지표(기본 임계값 T, linear gain) 한 줄
        for m in METHODS:
            res = results[m]
            rels = [rel_by_chunk[c.chunk_id] for c in res.chunks]
            rels_map[(query.id, m)] = rels
            ids_map[(query.id, m)] = [c.chunk_id for c in res.chunks]
            latency_acc[m].append(res.latency_ms)

            row = {
                "keyword": query.keyword,
                "query_id": query.id,
                "question": query.question,
                "method": m,
                "latency_ms": round(res.latency_ms, 2),
                "mrr": round(metrics.mrr(rels, T), 4),
            }
            for k in K_VALUES:
                row[f"p@{k}"] = round(metrics.precision_at_k(rels, k, T), 4)
                row[f"avgrel@{k}"] = round(metrics.mean_relevance_at_k(rels, k), 4)
                row[f"ndcg@{k}"] = round(metrics.ndcg_at_k(rels, pool_rels, k), 4)
            per_query_rows.append(row)

        print(f"  [{qi + 1}/{len(queries)}] {query.question}  (판정 청크 {len(pool)}개)")

    # 7) 집계 + 저장 + 출력
    summary = _summarize(rels_map, pool_map, ids_map, latency_acc, queries)
    summary["judge"] = {
        "parse_failures": judge.parse_failures,
        "api_failures": judge.api_failures,
        "total_scored": judge.total_scored,
    }
    _write_outputs(per_query_rows, summary)
    _print_summary(summary, skipped)


def _series(rels_map, pool_map, queries, metric, k=None, threshold=None, exp_gain=False):
    """쿼리 순서로 정렬된 {방식: [지표값]} 반환. 평균·paired 통계 양쪽에 공용으로 쓴다."""
    if threshold is None:
        threshold = metrics.RELEVANT_THRESHOLD
    out = {m: [] for m in METHODS}
    for q in queries:
        pool_rels = pool_map[q.id]
        for m in METHODS:
            rels = rels_map[(q.id, m)]
            if metric == "p":
                v = metrics.precision_at_k(rels, k, threshold)
            elif metric == "avgrel":
                v = metrics.mean_relevance_at_k(rels, k)
            elif metric == "ndcg":
                v = metrics.ndcg_at_k(rels, pool_rels, k, exp_gain)
            elif metric == "mrr":
                v = metrics.mrr(rels, threshold)
            else:
                raise ValueError(metric)
            out[m].append(v)
    return out


def _p95(sorted_vals: list[float]) -> float:
    """정렬된 리스트의 95 분위수(최근접 순위법). 비면 0."""
    if not sorted_vals:
        return 0.0
    idx = min(int(round(0.95 * (len(sorted_vals) - 1))), len(sorted_vals) - 1)
    return sorted_vals[idx]


def _summarize(rels_map, pool_map, ids_map, latency_acc, queries) -> dict:
    T = metrics.RELEVANT_THRESHOLD
    K = RAG_TOP_K

    # --- 대표 지표(threshold=T, linear gain) + latency median/p95 ---
    methods: dict[str, dict] = {m: {} for m in METHODS}
    mrr_s = _series(rels_map, pool_map, queries, "mrr", threshold=T)
    for k in K_VALUES:
        p_s = _series(rels_map, pool_map, queries, "p", k=k, threshold=T)
        a_s = _series(rels_map, pool_map, queries, "avgrel", k=k)
        n_s = _series(rels_map, pool_map, queries, "ndcg", k=k)
        for m in METHODS:
            methods[m][f"p@{k}"] = round(metrics.mean(p_s[m]), 4)
            methods[m][f"avgrel@{k}"] = round(metrics.mean(a_s[m]), 4)
            methods[m][f"ndcg@{k}"] = round(metrics.mean(n_s[m]), 4)
    for m in METHODS:
        methods[m]["mrr"] = round(metrics.mean(mrr_s[m]), 4)
        lat = sorted(latency_acc[m])
        methods[m]["latency_median_ms"] = round(statistics.median(lat), 1) if lat else 0.0
        methods[m]["latency_p95_ms"] = round(_p95(lat), 1)

    # --- 방식 간 결과 겹침 (Jaccard) ---
    overlap = {}
    for a, b in combinations(METHODS, 2):
        vals = [metrics.jaccard(ids_map[(q.id, a)], ids_map[(q.id, b)]) for q in queries]
        overlap[f"{a}-{b}"] = round(metrics.mean(vals), 4)

    # --- 유의성: hybrid vs dense/sparse, 핵심 지표에 paired 부트스트랩 CI ---
    ndcg_K = _series(rels_map, pool_map, queries, "ndcg", k=K)
    p_K = _series(rels_map, pool_map, queries, "p", k=K, threshold=T)
    significance = {}
    for base in ("dense", "sparse"):
        significance[f"hybrid_vs_{base}"] = {
            f"ndcg@{K}": metrics.paired_diff_ci(ndcg_K["hybrid"], ndcg_K[base]),
            f"p@{K}": metrics.paired_diff_ci(p_K["hybrid"], p_K[base]),
            "mrr": metrics.paired_diff_ci(mrr_s["hybrid"], mrr_s[base]),
        }

    # --- 민감도: threshold 1 vs 2, nDCG linear vs exp (캐시된 라벨 재사용, 재검색 불필요) ---
    sensitivity = {"threshold": {}, "ndcg_gain": {}}
    for t in (1, 2):
        p_t = _series(rels_map, pool_map, queries, "p", k=K, threshold=t)
        mrr_t = _series(rels_map, pool_map, queries, "mrr", threshold=t)
        sensitivity["threshold"][f"t={t}"] = {
            m: {f"p@{K}": round(metrics.mean(p_t[m]), 4), "mrr": round(metrics.mean(mrr_t[m]), 4)}
            for m in METHODS
        }
    for label, exp in (("linear", False), ("exp", True)):
        n_g = _series(rels_map, pool_map, queries, "ndcg", k=K, exp_gain=exp)
        sensitivity["ndcg_gain"][label] = {m: round(metrics.mean(n_g[m]), 4) for m in METHODS}

    return {
        "methods": methods,
        "overlap": overlap,
        "significance": significance,
        "sensitivity": sensitivity,
        "k_values": K_VALUES,
        "top_k": K,
        "threshold": T,
        "n_queries": len(queries),
    }


def _write_outputs(per_query_rows: list[dict], summary: dict) -> None:
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    csv_path = os.path.join(_RESULTS_DIR, "per_query.csv")
    if per_query_rows:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(per_query_rows[0].keys()))
            writer.writeheader()
            writer.writerows(per_query_rows)
        print(f"\n[저장] {csv_path}")
    else:
        print("\n[경고] 기록할 쿼리 결과가 없어 per_query.csv를 건너뜁니다.")

    summary_path = os.path.join(_RESULTS_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[저장] {summary_path}")


def _print_summary(summary: dict, skipped: list[str]) -> None:
    k_values = summary["k_values"]
    K = summary["top_k"]
    T = summary["threshold"]
    cols = (["method"] + [f"p@{k}" for k in k_values] + [f"avgrel@{k}" for k in k_values]
            + [f"ndcg@{k}" for k in k_values]
            + ["mrr", "latency_median_ms", "latency_p95_ms"])
    widths = {c: max(len(c), 10) for c in cols}
    lat_cols = {"latency_median_ms", "latency_p95_ms"}

    print("\n" + "=" * 60)
    print(f"검색 방식 비교 (평균, threshold={T}, n={summary['n_queries']})")
    print("=" * 60)
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("-" * len(header))
    for m, vals in summary["methods"].items():
        cells = [m.ljust(widths["method"])]
        for c in cols[1:]:
            cells.append(f"{vals[c]:.1f}".ljust(widths[c]) if c in lat_cols
                         else f"{vals[c]:.4f}".ljust(widths[c]))
        print("  ".join(cells))

    print("\n방식 간 결과 겹침 (Jaccard, 1에 가까울수록 세 방식이 같은 걸 뽑음):")
    for pair, val in summary["overlap"].items():
        print(f"  {pair}: {val:.3f}")

    print(f"\n유의성 (hybrid − baseline, 95% 부트스트랩 CI / n={summary['n_queries']}):")
    for pair, tests in summary["significance"].items():
        base = pair.replace("hybrid_vs_", "")
        print(f"  vs {base}:")
        for name, r in tests.items():
            mark = "유의✅" if r["significant"] else "무의미(차이없음 가능)"
            print(f"    {name:<8} Δ={r['mean_diff']:+.4f}  CI[{r['ci_low']:+.4f}, {r['ci_high']:+.4f}]  "
                  f"승={r['wins']}/{r['n']}  → {mark}")

    print(f"\n민감도 — threshold (p@{K}, mrr):")
    for t_label, per_m in summary["sensitivity"]["threshold"].items():
        cells = ", ".join(f"{m}: p@{K}={v[f'p@{K}']:.3f}/mrr={v['mrr']:.3f}" for m, v in per_m.items())
        print(f"  {t_label}: {cells}")
    print(f"민감도 — nDCG@{K} gain:")
    for g_label, per_m in summary["sensitivity"]["ndcg_gain"].items():
        cells = ", ".join(f"{m}={v:.4f}" for m, v in per_m.items())
        print(f"  {g_label}: {cells}")

    judge = summary.get("judge", {})
    total = judge.get("total_scored", 0)
    parse_fails = judge.get("parse_failures", 0)
    api_fails = judge.get("api_failures", 0)
    if total:
        total_fails = parse_fails + api_fails
        rate = total_fails / total * 100
        flag = "  ⚠ 실패율 높음 — 미판정 청크가 0으로 처리돼 지표가 낮게 나옴, 한산할 때 재실행 권장" if rate >= 5 else ""
        print(f"\n판정 실패: 총 {total_fails}/{total} ({rate:.1f}%)"
              f"  [파싱실패 {parse_fails} / API실패(NIM과부하) {api_fails}]{flag}")


if __name__ == "__main__":
    main()
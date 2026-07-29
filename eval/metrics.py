"""
metrics.py
검색 순위와 관련도(0/1/2 판정)로 지표를 계산한다.

라벨은 union 풀(세 방식이 뽑은 청크 전체)을 LLM으로 채점한 값이므로, nDCG의
IDCG는 그 풀에서 얻을 수 있는 이상적 순위 기준으로 계산한다(pooled nDCG).

- Precision@k : 상위 k개 중 관련(rel>=THRESHOLD) 비율
- AvgRel@k    : 상위 k개 관련도(0~2)의 평균 — 이진 기준 없이 등급을 그대로 반영
- nDCG@k      : 등급 관련도(gain=rel)를 반영한 순위 품질
- MRR         : 첫 관련(rel>=THRESHOLD) 문서의 역순위
"""

import math
import random

# rel 이 이 값 이상이면 "관련 있음"으로 본다 (Precision/MRR 용).
# 검색이 keyword 필터를 먼저 거치므로 후보가 전부 해당 밈 관련 문서다.
# 기준을 1(부분 관련)로 두면 거의 다 통과해 점수가 포화됨 → 2(명확히 관련)로 강화.
# 이 값은 기본값일 뿐이고, run_compare는 1/2 양쪽으로 민감도 분석을 돌린다.
RELEVANT_THRESHOLD = 2


def precision_at_k(rels: list[int], k: int, threshold: int = RELEVANT_THRESHOLD) -> float:
    """상위 k개 중 rel>=threshold 비율."""
    if k <= 0:
        return 0.0
    top = rels[:k]
    hits = sum(1 for r in top if r >= threshold)
    return hits / k


def mean_relevance_at_k(rels: list[int], k: int) -> float:
    """상위 k개 관련도(0~2)의 평균. 결과가 k개 미만이면 부족분은 0으로 채워 계산."""
    if k <= 0:
        return 0.0
    return sum(rels[:k]) / k


def _gain(rel: int, exp_gain: bool) -> float:
    """nDCG gain. linear=rel(0~2 스케일에 타당), exponential=2^rel-1(고관련 강조)."""
    return (2 ** rel - 1) if exp_gain else rel


def _dcg(rels: list[int], k: int, exp_gain: bool = False) -> float:
    return sum(_gain(rel, exp_gain) / math.log2(i + 2) for i, rel in enumerate(rels[:k]))


def ndcg_at_k(rels: list[int], pool_rels: list[int], k: int, exp_gain: bool = False) -> float:
    """
    rels      : 이 방식이 반환한 순서대로의 관련도
    pool_rels : 이 쿼리에서 판정된 모든 청크의 관련도(IDCG 기준)
    exp_gain  : True면 2^rel-1 gain(고관련 강조), False면 linear gain(기본)
    """
    idcg = _dcg(sorted(pool_rels, reverse=True), k, exp_gain)
    if idcg == 0:
        return 0.0
    return _dcg(rels, k, exp_gain) / idcg


def mrr(rels: list[int], threshold: int = RELEVANT_THRESHOLD) -> float:
    """첫 rel>=threshold 문서의 역순위. 없으면 0."""
    for i, r in enumerate(rels):
        if r >= threshold:
            return 1.0 / (i + 1)
    return 0.0


def paired_diff_ci(
    a: list[float], b: list[float], n_boot: int = 2000, alpha: float = 0.05, seed: int = 0
) -> dict:
    """
    쿼리 순서로 정렬된 두 방식의 지표(paired)로 평균차 a-b의 부트스트랩 신뢰구간을 낸다.

    n=45 수준의 소표본에서 "hybrid가 0.02 이겼다"가 신호인지 노이즈인지 구분하는 최소 장치.
    ci가 0을 포함하면 유의하지 않은 차이(= "차이 없음"도 정당한 결론).

    반환: {mean_diff, ci_low, ci_high, significant, n, wins} — wins는 a>b인 쿼리 수.
    """
    n = min(len(a), len(b))
    if n == 0:
        return {"mean_diff": 0.0, "ci_low": 0.0, "ci_high": 0.0,
                "significant": False, "n": 0, "wins": 0}
    diffs = [a[i] - b[i] for i in range(n)]
    rng = random.Random(seed)
    boot_means = []
    for _ in range(n_boot):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        boot_means.append(sum(sample) / n)
    boot_means.sort()
    lo = boot_means[int((alpha / 2) * n_boot)]
    hi = boot_means[min(int((1 - alpha / 2) * n_boot), n_boot - 1)]
    return {
        "mean_diff": round(sum(diffs) / n, 4),
        "ci_low": round(lo, 4),
        "ci_high": round(hi, 4),
        "significant": (lo > 0 or hi < 0),  # CI가 0을 배제하면 유의
        "n": n,
        "wins": sum(1 for d in diffs if d > 0),
    }


def jaccard(a: list[str], b: list[str]) -> float:
    """두 결과 id 집합의 겹침(합집합 대비 교집합). 둘 다 비면 0."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
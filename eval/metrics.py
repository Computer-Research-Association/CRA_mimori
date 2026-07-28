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

# rel 이 이 값 이상이면 "관련 있음"으로 본다 (Precision/MRR 용).
# 검색이 keyword 필터를 먼저 거치므로 후보가 전부 해당 밈 관련 문서다.
# 기준을 1(부분 관련)로 두면 거의 다 통과해 점수가 포화됨 → 2(명확히 관련)로 강화.
RELEVANT_THRESHOLD = 2


def precision_at_k(rels: list[int], k: int) -> float:
    """상위 k개 중 rel>=THRESHOLD 비율."""
    if k <= 0:
        return 0.0
    top = rels[:k]
    hits = sum(1 for r in top if r >= RELEVANT_THRESHOLD)
    return hits / k


def mean_relevance_at_k(rels: list[int], k: int) -> float:
    """상위 k개 관련도(0~2)의 평균. 결과가 k개 미만이면 부족분은 0으로 채워 계산."""
    if k <= 0:
        return 0.0
    return sum(rels[:k]) / k


def _dcg(rels: list[int], k: int) -> float:
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(rels[:k]))


def ndcg_at_k(rels: list[int], pool_rels: list[int], k: int) -> float:
    """
    rels      : 이 방식이 반환한 순서대로의 관련도
    pool_rels : 이 쿼리에서 판정된 모든 청크의 관련도(IDCG 기준)
    """
    idcg = _dcg(sorted(pool_rels, reverse=True), k)
    if idcg == 0:
        return 0.0
    return _dcg(rels, k) / idcg


def mrr(rels: list[int]) -> float:
    """첫 rel>=THRESHOLD 문서의 역순위. 없으면 0."""
    for i, r in enumerate(rels):
        if r >= RELEVANT_THRESHOLD:
            return 1.0 / (i + 1)
    return 0.0


def jaccard(a: list[str], b: list[str]) -> float:
    """두 결과 id 집합의 겹침(합집합 대비 교집합). 둘 다 비면 0."""
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
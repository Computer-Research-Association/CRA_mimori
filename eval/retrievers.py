"""
retrievers.py
analysis/rag_pipeline.py의 세 검색 함수(dense/sparse/hybrid)를 통일된 인터페이스로
감싼다. run_compare가 방식 이름만 바꿔가며 동일하게 호출할 수 있게 하고, 검색
지연시간(latency)도 함께 잰다.

쿼리 임베딩(dense_vec, sparse dict)은 호출부에서 미리 만들어 넘긴다 — BGE-M3를
쿼리마다 재로드하지 않고 한 번에 인코딩하기 위함.
"""

import time
from dataclasses import dataclass

from analysis.rag_pipeline import (
    search_dense_only,
    search_relevant_chunks,
    search_sparse_only,
)
from config.config_cilent import RAG_TOP_K

# 비교 대상 세 방식의 표준 이름 (결과 표의 열 이름으로도 쓰임)
METHODS = ("dense", "sparse", "hybrid")


@dataclass
class RetrievedChunk:
    """검색 결과 청크 하나 (순위대로 담김)."""
    chunk_id: str
    score: float
    text: str
    title: str
    url: str


@dataclass
class RetrievalResult:
    """한 (쿼리, 방식)에 대한 검색 결과 + 지연시간(ms)."""
    method: str
    chunks: list[RetrievedChunk]
    latency_ms: float


def _to_chunks(points) -> list[RetrievedChunk]:
    """Qdrant ScoredPoint 리스트를 RetrievedChunk 리스트로 변환."""
    chunks = []
    for p in points:
        payload = p.payload or {}
        chunks.append(
            RetrievedChunk(
                chunk_id=str(p.id),
                score=float(p.score),
                text=payload.get("text", ""),
                title=payload.get("title") or "제목 없음",
                url=payload.get("url") or "출처 없음",
            )
        )
    return chunks


def retrieve(
    method: str,
    keyword: str,
    dense_vec: list[float],
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
) -> RetrievalResult:
    """
    method("dense"/"sparse"/"hybrid")에 맞는 검색을 실행하고 결과+latency 반환.
    """
    start = time.perf_counter()
    if method == "dense":
        points = search_dense_only(keyword, dense_vec, top_k=top_k)
    elif method == "sparse":
        points = search_sparse_only(keyword, sparse, top_k=top_k)
    elif method == "hybrid":
        points = search_relevant_chunks(keyword, dense_vec, sparse, top_k=top_k)
    else:
        raise ValueError(f"알 수 없는 검색 방식: {method!r} (가능: {METHODS})")
    latency_ms = (time.perf_counter() - start) * 1000.0

    return RetrievalResult(method=method, chunks=_to_chunks(points), latency_ms=latency_ms)
"""
rag_pipeline.py
질문 임베딩을 Qdrant에서 하이브리드(dense+sparse) 검색해 관련 청크를 찾고,
그 청크를 근거로 LLM에 넘길 프롬프트를 조립한다.
"""

from qdrant_client.http import models

from config.config_cilent import (
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
    RAG_PROMPT_PATH,
    RAG_TOP_K,
)
from DB.drant_clitent import client
from embedding.pipeline import _to_sparse_vector
from trend.trend_service import format_trend_context


def _build_filter(keyword: str, sources: list[str] | None = None) -> models.Filter:
    """keyword(필수) + source(선택, 예: tavily 제외하고 dcinside/natepann만) 필터.

    sources가 None이면 소스 제한 없이 keyword만 필터링한다(기존 동작과 동일)."""
    must = [models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    if sources:
        must.append(models.FieldCondition(key="source", match=models.MatchAny(any=sources)))
    return models.Filter(must=must)


def search_relevant_chunks(
    keyword: str,
    dense_vec: list[float],
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
    sources: list[str] | None = None,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword(+ sources 지정 시 그 소스만)로
    필터링한 뒤, dense+sparse 하이브리드 검색(RRF fusion)으로 상위 top_k개 포인트를 반환.
    """
    keyword_filter = _build_filter(keyword, sources)

    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        prefetch=[
            models.Prefetch(
                query=dense_vec,
                using=QDRANT_DENSE_VECTOR_NAME,
                filter=keyword_filter,
                limit=top_k * 2,
            ),
            models.Prefetch(
                query=_to_sparse_vector(sparse),
                using=QDRANT_SPARSE_VECTOR_NAME,
                filter=keyword_filter,
                limit=top_k * 2,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=keyword_filter,
        limit=top_k,
        with_payload=True,
    )
    return response.points


def search_dense_only(
    keyword: str,
    dense_vec: list[float],
    top_k: int = RAG_TOP_K,
    sources: list[str] | None = None,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword(+ sources 지정 시 그 소스만)로
    필터링한 뒤, dense 벡터만으로(sparse/융합 없이) 상위 top_k개 포인트를 반환.
    순수 의미 유사도 검색 결과만 확인하고 싶을 때 사용한다.
    """
    keyword_filter = _build_filter(keyword, sources)

    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=dense_vec,
        using=QDRANT_DENSE_VECTOR_NAME,
        query_filter=keyword_filter,
        limit=top_k,
        with_payload=True,
    )
    return response.points


def search_sparse_only(
    keyword: str,
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
    sources: list[str] | None = None,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword(+ sources 지정 시 그 소스만)로
    필터링한 뒤, sparse(lexical) 벡터만으로(dense/융합 없이) 상위 top_k개 포인트를 반환.
    단어 일치 기반 유사도 검색 결과만 확인하고 싶을 때 사용한다.
    """
    keyword_filter = _build_filter(keyword, sources)

    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=_to_sparse_vector(sparse),
        using=QDRANT_SPARSE_VECTOR_NAME,
        query_filter=keyword_filter,
        limit=top_k,
        with_payload=True,
    )
    return response.points


_CONTEXT_SEPARATOR = "\n\n---\n\n"


def build_rag_prompt(keyword: str, question: str, points: list[models.ScoredPoint]) -> str:
    """
    rag_prompt_template.md를 읽어 {keyword}/{context}/{question}/{trend_info}를 채운 문자열 반환.
    {context}는 각 포인트를 '[출처: {title} / {url}]\n{text}' 형태로 만들어 이어붙인 것.
    """
    with open(RAG_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    context_parts = []
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = point.payload.get("url") or "출처 없음"
        text = point.payload.get("text", "")
        context_parts.append(f"[출처: {title} / {url}]\n{text}")

    context = _CONTEXT_SEPARATOR.join(context_parts)
    trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, context=context, question=question, trend_info=trend_info)

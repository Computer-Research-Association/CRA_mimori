"""
rag_pipeline.py
질문 임베딩을 Qdrant에서 하이브리드(dense+sparse) 검색해 관련 청크를 찾고,
그 청크를 근거로 LLM에 넘길 프롬프트를 조립한다.
"""

import time

from qdrant_client.http import models
from qdrant_client.http.exceptions import ResponseHandlingException

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


def _query_points_with_retry(max_retries: int = 3, base_delay: float = 2.0, **kwargs):
    """client.query_points()를 호출하되, SSH 터널의 유휴 연결이 끊겨 발생하는
    ResponseHandlingException(예: WinError 10054)이면 지수 백오프로 재시도한다."""
    for attempt in range(1, max_retries + 1):
        try:
            return client.query_points(**kwargs)
        except ResponseHandlingException as e:
            if attempt == max_retries:
                raise
            wait = base_delay * (2 ** (attempt - 1))
            print(f"[rag_pipeline] Qdrant 연결 오류(터널 끊김 의심), {wait:.0f}초 후 재시도 ({attempt}/{max_retries}): {e}")
            time.sleep(wait)


def _build_filter(
    keyword: str,
    sources: list[str] | None = None,
    is_relevant: bool | None = None,
) -> models.Filter:
    """keyword(필수) + source(선택) + is_relevant(선택) 필터.

    sources가 None이면 소스 제한 없이 keyword만 필터링한다(기존 동작과 동일).
    is_relevant=True → 오염 판정된 청크 제외. None이면 필드 유무 무관(기존 동작과 동일)."""
    must = [models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    if sources:
        must.append(models.FieldCondition(key="source", match=models.MatchAny(any=sources)))
    if is_relevant is not None:
        must.append(models.FieldCondition(key="is_relevant", match=models.MatchValue(value=is_relevant)))
    return models.Filter(must=must)


# search_relevant_chunks의 기본값. 호출부(rag_main.py 등)가 min_length/over_fetch_factor를
# 안 넘기면 이 값으로 동작해 기존 동작과 호환된다.
_DEFAULT_MIN_CHUNK_LENGTH = 30
_DEFAULT_OVER_FETCH_FACTOR = 3


_DECORATIVE_CHARS = " ~"  # 공백/물결표(강조용 반복 표기)는 매칭 전에 제거


def _normalize_for_match(text: str) -> str:
    """공백과 물결표(~)를 지운다. "좋~다~"(키워드) vs "좋다~~~"(실제 게시글, 물결
    개수가 다름)처럼 강조 표기 차이 때문에 매칭이 실패하는 걸 방지한다."""
    return "".join(ch for ch in text if ch not in _DECORATIVE_CHARS)


def _is_valid_chunk(point: models.ScoredPoint, keyword: str, min_length: int) -> bool:
    """크롤러가 매긴 keyword 태그를 그대로 믿지 않고, title/text(공백·물결표 무시)에
    실제로 그 키워드가 있는지 + 텍스트가 최소 길이 이상인지 재검증한다.

    - 길이 체크: "야르\\n- dc official App"처럼 정보 없는 한 줄짜리 청크 배제.
    - 키워드 체크: 크롤러가 무관한 문서를 잘못 태깅한 경우(예: 완전 무관한 여행기가
      keyword="거제야호"로 저장된 사례) 배제. 공백/물결표를 제거하고 비교해 "거제 야호"
      (공백 차이)나 "좋다~~~"(물결 개수 차이)처럼 단순 표기 차이로 진짜 관련 있는
      문서까지 같이 걸러지는 걸 방지한다.
    """
    text = point.payload.get("text", "")
    title = point.payload.get("title", "")
    if len(text.strip()) < min_length:
        return False
    kw_norm = _normalize_for_match(keyword)
    combined = _normalize_for_match(text + title)
    return kw_norm in combined


_DEFAULT_MIN_DENSE_SCORE = 0.0  # 0.0=비활성(기존 동작과 호환). 양수로 주면 dense 유사도 하한선 적용.
_DEFAULT_MAX_PER_SOURCE = None  # None=비활성(기존 동작과 호환). 정수로 주면 소스당 최대 개수 제한.


def _select_with_source_cap(
    valid: list[models.ScoredPoint], top_k: int, max_per_source: int | None
) -> list[models.ScoredPoint]:
    """점수 내림차순인 valid에서, 한 소스가 top_k를 독점하지 못하도록 소스당
    max_per_source개까지만 우선 채운다. 한도 내에서 top_k가 안 채워지면,
    한도를 넘긴 나머지(leftover)로 부족분을 채워 빈 자리를 최소화한다.
    """
    if not max_per_source:
        return valid[:top_k]

    selected = []
    source_counts: dict[str, int] = {}
    leftover = []
    for p in valid:
        source = p.payload.get("source")
        if source_counts.get(source, 0) < max_per_source:
            selected.append(p)
            source_counts[source] = source_counts.get(source, 0) + 1
        else:
            leftover.append(p)
        if len(selected) >= top_k:
            break

    if len(selected) < top_k:
        selected += leftover[: top_k - len(selected)]

    return selected[:top_k]


def search_relevant_chunks(
    keyword: str,
    dense_vec: list[float],
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
    sources: list[str] | None = None,
    is_relevant: bool | None = None,
    min_length: int = _DEFAULT_MIN_CHUNK_LENGTH,
    over_fetch_factor: int = _DEFAULT_OVER_FETCH_FACTOR,
    min_dense_score: float = _DEFAULT_MIN_DENSE_SCORE,
    max_per_source: int | None = _DEFAULT_MAX_PER_SOURCE,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword(+ sources 지정 시 그 소스만)로
    필터링한 뒤, dense+sparse 하이브리드 검색(RRF fusion)으로 후보를 뽑고, 정보가 없거나
    (min_length 미만) 크롤링 오염이 의심되는(키워드 미포함) 청크를 제외한 뒤 상위
    top_k개를 반환.

    is_relevant=True → Qdrant 필터 단계에서 오염 판정 청크를 제외.
    over_fetch_factor: 필터링으로 줄어들 걸 감안해 top_k*over_fetch_factor개까지
    넉넉히 후보를 받아온다.
    """
    keyword_filter = _build_filter(keyword, sources, is_relevant)
    raw_limit = top_k * over_fetch_factor

    dense_score_map = {}
    if min_dense_score > 0:
        dense_response = _query_points_with_retry(
            collection_name=QDRANT_COLLECTION,
            query=dense_vec,
            using=QDRANT_DENSE_VECTOR_NAME,
            query_filter=keyword_filter,
            limit=raw_limit * 2,
            with_payload=False,
        )
        dense_score_map = {p.id: p.score for p in dense_response.points}

    response = _query_points_with_retry(
        collection_name=QDRANT_COLLECTION,
        prefetch=[
            models.Prefetch(
                query=dense_vec,
                using=QDRANT_DENSE_VECTOR_NAME,
                filter=keyword_filter,
                limit=raw_limit * 2,
            ),
            models.Prefetch(
                query=_to_sparse_vector(sparse),
                using=QDRANT_SPARSE_VECTOR_NAME,
                filter=keyword_filter,
                limit=raw_limit * 2,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=keyword_filter,
        limit=raw_limit,
        with_payload=True,
    )

    candidates = response.points
    valid_ids = set()
    valid = []
    for p in candidates:
        if min_dense_score > 0 and dense_score_map.get(p.id, 0.0) < min_dense_score:
            continue
        if _is_valid_chunk(p, keyword, min_length):
            valid.append(p)
            valid_ids.add(p.id)

    selected = _select_with_source_cap(valid, top_k, max_per_source)

    if len(selected) < top_k:
        selected_ids = {p.id for p in selected}
        rejected = [p for p in candidates if p.id not in selected_ids]
        selected += rejected[: top_k - len(selected)]

    return selected[:top_k]


def search_dense_only(
    keyword: str,
    dense_vec: list[float],
    top_k: int = RAG_TOP_K,
    sources: list[str] | None = None,
    is_relevant: bool | None = None,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword(+ sources 지정 시 그 소스만)로
    필터링한 뒤, dense 벡터만으로(sparse/융합 없이) 상위 top_k개 포인트를 반환.
    순수 의미 유사도 검색 결과만 확인하고 싶을 때 사용한다.
    """
    keyword_filter = _build_filter(keyword, sources, is_relevant)

    response = _query_points_with_retry(
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
    is_relevant: bool | None = None,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword(+ sources 지정 시 그 소스만)로
    필터링한 뒤, sparse(lexical) 벡터만으로(dense/융합 없이) 상위 top_k개 포인트를 반환.
    단어 일치 기반 유사도 검색 결과만 확인하고 싶을 때 사용한다.
    """
    keyword_filter = _build_filter(keyword, sources, is_relevant)

    response = _query_points_with_retry(
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

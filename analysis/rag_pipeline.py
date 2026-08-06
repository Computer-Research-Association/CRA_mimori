"""
rag_pipeline.py
질문 임베딩을 Qdrant에서 하이브리드(dense+sparse) 검색해 관련 청크를 찾고,
그 청크를 근거로 LLM에 넘길 프롬프트를 조립한다.
"""

import difflib
import time
from urllib.parse import parse_qs, unquote, urlparse

from qdrant_client.http import models
from qdrant_client.http.exceptions import ResponseHandlingException

from config.config_cilent import (
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
    RAG_FACET_MERGED_MAX_PER_SOURCE,
    RAG_FACET_MIN_MERGED_TOTAL,
    RAG_FACET_NEAR_DUP_THRESHOLD,
    RAG_FACET_PROMPT_PATH,
    RAG_PROMPT_PATH,
    RAG_TOP_K,
)
from DB.drant_clitent import client
from embedding.pipeline import _to_sparse_vector
from trend.trend_service import format_trend_context


# 인용/프롬프트에 노출할 출처 URL 대체 문자열(정상 링크가 없을 때).
NO_SOURCE_URL = "링크 없음"


def clean_source_url(raw: str | None) -> str:
    """저장된 출처 URL을 표시 가능한 절대 http(s) URL로 정규화한다.

    Tavily 등 일부 소스가 정상 URL 대신 리다이렉트/상대경로 링크
    (예: '/goto?url=CAESZg...%3D%3D')를 반환해 인용에 클릭 불가한 깨진 링크가
    노출되는 문제를 막는다.

    - 정상 절대 URL(scheme http/https + netloc) → 그대로 반환
    - 리다이렉트 래퍼(?url=/?q=/?u= 안에 퍼센트인코딩된 실제 URL) → 실제 URL 추출
    - 그 외(상대경로, 호스트 없음, 복원 불가한 불투명 리다이렉트 토큰 등) → NO_SOURCE_URL

    복원 불가한 경우 원본 문자열을 그대로 남기지 않고 NO_SOURCE_URL을 돌려주어,
    호출부가 깨진 링크 대신 '링크 없음'을 표시하게 한다.
    """
    if not raw:
        return NO_SOURCE_URL
    raw = raw.strip()
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return raw
    # 리다이렉트 래퍼에서 퍼센트인코딩된 실제 URL 복원 시도.
    for key in ("url", "q", "u"):
        values = parse_qs(parsed.query).get(key)
        if not values:
            continue
        target = unquote(values[0])
        target_parsed = urlparse(target)
        if target_parsed.scheme in ("http", "https") and target_parsed.netloc:
            return target
    return NO_SOURCE_URL


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


def build_rag_prompt(
    keyword: str,
    question: str,
    points: list[models.ScoredPoint],
    trend_info: str | None = None,
) -> str:
    """
    rag_prompt_template.md를 읽어 {keyword}/{context}/{question}/{trend_info}를 채운 문자열 반환.
    {context}는 각 포인트를 '[출처: {title} / {url}]\n{text}' 형태로 만들어 이어붙인 것.
    """
    with open(RAG_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    context_parts = []
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = clean_source_url(point.payload.get("url"))
        text = point.payload.get("text", "")
        context_parts.append(f"[출처: {title} / {url}]\n{text}")

    context = _CONTEXT_SEPARATOR.join(context_parts)
    # trend_info=None이면 여기서 조회. 이미 계산해둔 값(빈 문자열 포함)을 넘기면
    # 그대로 써서 중복 조회를 막는다(rag_main.py가 콘솔 출력과 공유할 때 사용).
    if trend_info is None:
        trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, context=context, question=question, trend_info=trend_info)


# ─────────────────────────────────────────────────────────────────────────────
# facet(다각도) 검색: 밈 하나를 의미/유행_이유/사용법/사용자층 네 각도로 나눠 각각
# 검색한 뒤, point.id 중복·근접 중복(미러링)·소스 쏠림을 제거해 하나의 컨텍스트로 병합한다.
# langchain_playground.ipynb의 셀 4(단일 키워드)와 셀 8(일괄 평가)에 글자 그대로 중복돼
# 있던 병합/중복제거/소스캡 로직을 여기로 모아 단일 진실 원천으로 만든 것.
# ─────────────────────────────────────────────────────────────────────────────


def default_facet_config(keyword: str) -> dict:
    """의미/유행_이유/사용법/사용자층 4-facet 기본 설정을 만든다.

    question은 f"{keyword}, ..."처럼 쉼표로 키워드를 앞에 붙여, 은/는 조사 활용(받침
    유무·특수문자 키워드) 문제 없이 sparse(lexical) 검색이 키워드 토큰을 인식하게 한다.
    파라미터를 바꿔 실험하려면 이 dict를 복사해 수정한 뒤 facet_search에 넘기면 된다.
    """
    common = {"top_k": 5, "min_length": 30, "over_fetch_factor": 3, "min_dense_score": 0.3, "max_per_source": 2}
    return {
        "의미":     {"question": f"{keyword}, 무슨 의미?", **common},
        "유행_이유": {"question": f"{keyword}, 왜 유행했나요?", **common},
        "사용법":   {"question": f"{keyword}, 어떻게 사용하나요?", **common},
        "사용자층": {"question": f"{keyword}, 주로 누가 사용하나요?", **common},
    }


def encode_facets(facet_config: dict) -> dict:
    """facet_config의 question들을 한 번에 배치 임베딩해 facet별 dense/sparse 벡터를 만든다.

    반환: {facet_name: {"dense": [...], "sparse": {...}}}.
    encode_batch는 무거운 BGE-M3를 지연 로드하므로, 임베딩이 필요 없는 호출 경로에서
    rag_pipeline을 import할 때 모델이 로드되지 않도록 함수 안에서 지연 import한다.
    """
    from embedding.encoder import encode_batch

    names = list(facet_config.keys())
    dense_vecs, lexical_weights = encode_batch([facet_config[n]["question"] for n in names])
    return {n: {"dense": d, "sparse": s} for n, d, s in zip(names, dense_vecs, lexical_weights)}


def _is_near_duplicate(text: str, accepted_texts: list[str], threshold: float) -> bool:
    """이미 채택된 텍스트 중 하나와 threshold 이상 유사하면 재게시(미러링)로 보고 True.
    예: 네이버블로그 in.naver.com / blog.naver.com 미러링처럼 URL만 다르고 내용이 같은 청크."""
    norm = text.strip()
    for other in accepted_texts:
        if difflib.SequenceMatcher(None, norm, other).ratio() >= threshold:
            return True
    return False


def merge_facet_results(
    facet_points: dict[str, list[models.ScoredPoint]],
    facet_order: list[str],
    merged_max_per_source: int = RAG_FACET_MERGED_MAX_PER_SOURCE,
    min_merged_total: int = RAG_FACET_MIN_MERGED_TOTAL,
    near_dup_threshold: float = RAG_FACET_NEAR_DUP_THRESHOLD,
) -> tuple[list[models.ScoredPoint], dict]:
    """facet별 검색 결과를 하나의 컨텍스트로 병합한다.

    1. point.id가 겹치는 청크(여러 facet에서 동시에 뽑힌 것)를 제거
    2. difflib로 근접 중복(다른 URL이지만 내용이 사실상 같은 미러링)을 제거
    3. facet 전체 합산 기준으로 소스당 merged_max_per_source개까지만 채움(쏠림 방지).
       한도 때문에 min_merged_total보다 적어지면 보류분(deferred)으로 그만큼 보충.

    반환: (merged_points, diagnostics). diagnostics는 노트북 5번 셀의 진단 출력용:
      - point_facets: {point_id: [뽑힌 facet 이름들]}
      - near_dup_skipped: 근접 중복으로 제외된 개수
      - deferred: 소스 상한으로 보류된 개수
      - source_counts: 최종 병합 결과의 소스별 개수
    """
    merged_points: list[models.ScoredPoint] = []
    seen_ids: set = set()
    accepted_texts: list[str] = []
    merged_source_counts: dict[str, int] = {}
    deferred: list[models.ScoredPoint] = []
    point_facets: dict = {}
    near_dup_skipped = 0

    for name in facet_order:
        for p in facet_points[name]:
            point_facets.setdefault(p.id, []).append(name)
            if p.id in seen_ids:
                continue
            text = p.payload.get("text", "")
            if _is_near_duplicate(text, accepted_texts, near_dup_threshold):
                near_dup_skipped += 1
                continue
            source = p.payload.get("source")
            if merged_source_counts.get(source, 0) >= merged_max_per_source:
                deferred.append(p)
                continue
            seen_ids.add(p.id)
            accepted_texts.append(text.strip())
            merged_source_counts[source] = merged_source_counts.get(source, 0) + 1
            merged_points.append(p)

    # 소스 상한 때문에 너무 적게 남았으면, 보류분(상한 넘긴 것)으로 최소 개수까지 채움
    if len(merged_points) < min_merged_total:
        for p in deferred:
            if p.id in seen_ids:
                continue
            text = p.payload.get("text", "")
            if _is_near_duplicate(text, accepted_texts, near_dup_threshold):
                continue
            seen_ids.add(p.id)
            accepted_texts.append(text.strip())
            merged_points.append(p)
            if len(merged_points) >= min_merged_total:
                break

    diagnostics = {
        "point_facets": point_facets,
        "near_dup_skipped": near_dup_skipped,
        "deferred": len(deferred),
        "source_counts": merged_source_counts,
    }
    return merged_points, diagnostics


def facet_search(
    keyword: str,
    facet_config: dict | None = None,
    facet_vectors: dict | None = None,
    sources: list[str] | None = None,
    is_relevant: bool | None = None,
    merged_max_per_source: int = RAG_FACET_MERGED_MAX_PER_SOURCE,
    min_merged_total: int = RAG_FACET_MIN_MERGED_TOTAL,
    near_dup_threshold: float = RAG_FACET_NEAR_DUP_THRESHOLD,
) -> tuple[list[models.ScoredPoint], dict]:
    """키워드를 facet_config의 각 각도로 검색해 병합된 컨텍스트를 반환한다.

    facet_config가 None이면 default_facet_config(keyword)를 쓴다.
    facet_vectors가 None이면 내부에서 encode_facets로 임베딩한다. rag_main처럼 임베딩
    직후 unload_model()로 VRAM을 비워야 하는 경로에서는, 밖에서 encode_facets→unload_model을
    먼저 한 뒤 그 결과를 facet_vectors로 넘겨 unload 타이밍을 제어한다.

    is_relevant=None(기본)은 노트북 실험 경로와 동일(오염 필터 미적용). 운영 경로에서
    오염 판정 청크를 Qdrant 단계에서 빼려면 is_relevant=True로 준다.

    반환: (merged_points, diagnostics). diagnostics는 merge_facet_results 참고.
    """
    if facet_config is None:
        facet_config = default_facet_config(keyword)
    if facet_vectors is None:
        facet_vectors = encode_facets(facet_config)

    facet_order = list(facet_config.keys())
    facet_points: dict[str, list[models.ScoredPoint]] = {}
    for name in facet_order:
        cfg = facet_config[name]
        vecs = facet_vectors[name]
        facet_points[name] = search_relevant_chunks(
            keyword, vecs["dense"], vecs["sparse"],
            top_k=cfg["top_k"], sources=sources, is_relevant=is_relevant,
            min_length=cfg["min_length"], over_fetch_factor=cfg["over_fetch_factor"],
            min_dense_score=cfg["min_dense_score"], max_per_source=cfg["max_per_source"],
        )

    merged_points, diagnostics = merge_facet_results(
        facet_points, facet_order,
        merged_max_per_source=merged_max_per_source,
        min_merged_total=min_merged_total,
        near_dup_threshold=near_dup_threshold,
    )
    diagnostics["facet_points"] = facet_points  # 노트북 5번 셀의 facet별 출력용
    return merged_points, diagnostics


def build_facet_prompt(
    keyword: str,
    points: list[models.ScoredPoint],
    trend_info: str | None = None,
) -> str:
    """rag_facet_prompt_template.md를 읽어 {keyword}/{trend_info}/{context}를 채운다.

    자유질문용 build_rag_prompt와 달리 {question}이 없고(4항목 고정 지시), context의 각
    자료 앞에 '소스유형'을 붙여 LLM이 1차 자료(커뮤니티)와 2차 자료(가공 콘텐츠)를 구분하게 한다.
    trend_info=None이면 keyword로 조회해 채운다. 넣지 않으려면 빈 문자열("")을 명시적으로 넘긴다.
    """
    with open(RAG_FACET_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    context_parts = []
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = clean_source_url(point.payload.get("url"))
        source = point.payload.get("source") or "알수없음"
        text = point.payload.get("text", "")
        context_parts.append(f"[출처: {title} / {url} / 소스유형: {source}]\n{text}")

    context = _CONTEXT_SEPARATOR.join(context_parts)
    if trend_info is None:
        trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, context=context, trend_info=trend_info)

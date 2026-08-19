"""
analysis/pipeline.py
이미 임베딩된 밈 키워드 목록 조회, Qdrant 청크 조회, 프롬프트 조립, LLM 분석 호출.
"""

import re
import time
from typing import Callable

from langchain_nvidia_ai_endpoints import ChatNVIDIA
from qdrant_client.http import models

from config.config_cilent import (
    ANALYSIS_MODEL,
    ANALYSIS_PROMPT_PATH,
    CLEANED_COLLECTION,
    CRAWL_REQUESTS_COLLECTION,
    HIDDEN_KEYWORDS_COLLECTION,
    LLM_REQUESTS_COLLECTION,
    NIM_KEY,
    QDRANT_COLLECTION,
    TREND_COLLECTION,
)
from DB.drant_clitent import ensure_collection, get_client
from DB.mongo_client import get_collection
from trend.trend_service import format_trend_context


def list_analyzable_keywords() -> list[str]:
    """memes 컬렉션에서 is_embedded=True인 문서들의 distinct keyword 목록 반환 (정렬됨).

    숨김 여부와 무관하게 전체 목록이다 — 온디맨드 크롤 요청의 "이미 존재하는 키워드"
    판정(routes.crawl_request_endpoint)이나 CLI 스크립트(analyze_main.py 등)는 숨긴
    키워드도 실존 데이터로 취급해야 하므로 여기서 걸러내면 안 된다. 화면에 보여줄
    목록만 걸러내려면 list_visible_keywords()를 쓴다.
    """
    collection = get_collection()
    keywords = collection.distinct("keyword", {"is_embedded": True})
    return sorted(keywords)


def list_hidden_keywords() -> list[str]:
    """숨김 처리된 키워드 목록 (정렬됨)."""
    collection = get_collection(HIDDEN_KEYWORDS_COLLECTION)
    return sorted(doc["_id"] for doc in collection.find({}, {"_id": 1}))


def list_visible_keywords() -> list[str]:
    """list_analyzable_keywords()에서 숨김 처리된 키워드를 뺀 목록. 검색창 등 사용자용 목록에 쓴다."""
    hidden = set(list_hidden_keywords())
    return [k for k in list_analyzable_keywords() if k not in hidden]


def hide_keyword(keyword: str) -> None:
    """키워드를 검색 목록에서 숨긴다. 데이터는 그대로 남고, unhide_keyword로 되돌릴 수 있다."""
    get_collection(HIDDEN_KEYWORDS_COLLECTION).update_one(
        {"_id": keyword}, {"$setOnInsert": {"_id": keyword}}, upsert=True
    )


def unhide_keyword(keyword: str) -> None:
    """숨김을 해제해 다시 검색 목록에 보이게 한다."""
    get_collection(HIDDEN_KEYWORDS_COLLECTION).delete_one({"_id": keyword})


def merge_keyword(source: str, target: str) -> dict:
    """source의 콘텐츠 데이터를 전부 target 소속으로 옮기고 source는 숨긴다.

    "거제야호"/"거제 야호~"/"야호~"처럼 같은 밈이 표기 차이로 따로 등록된
    중복을 정리하는 용도. 실제 삭제는 하지 않는다 — source를 숨겨서 검색·
    관리자 표에서 안 보이게만 하고, 되돌릴 수 없는 삭제는 이미 검증된
    "숨긴 키워드 완전삭제" 흐름(delete_keyword_permanently)에 맡긴다. 그러면
    병합이 잘못됐을 때 unhide_keyword로 되돌릴 여지가 남는다.

    trend_scores(날짜별 z-score 이력)는 옮기지 않는다 — source/target 둘 다
    같은 날짜의 판정을 이미 갖고 있을 수 있어서, keyword만 바꿔치기하면
    (keyword, date) 조합이 중복돼 이력이 뒤섞인다. source가 숨겨지면 어차피
    트렌드 화면에 안 보이므로, target의 기존 이력을 그대로 두는 게 안전하다.
    """
    if source == target:
        raise ValueError("병합 대상과 원본 키워드가 같습니다")

    get_collection().update_many({"keyword": source}, {"$set": {"keyword": target}})
    for name in (CLEANED_COLLECTION, LLM_REQUESTS_COLLECTION):
        get_collection(name).update_many({"keyword": source}, {"$set": {"keyword": target}})

    get_client().set_payload(
        collection_name=QDRANT_COLLECTION,
        payload={"keyword": target},
        points=models.Filter(
            must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=source))]
        ),
    )

    hide_keyword(source)
    return {"source": source, "target": target}


def delete_keyword_permanently(keyword: str) -> None:
    """키워드와 관련된 데이터를 전부 지운다. 되돌릴 수 없다.

    memes/cleaned_memes/trend_scores는 (keyword, ...) 조합으로 문서가 여러 개
    쌓이므로 delete_many를 쓴다. crawl_requests/hidden_keywords는 keyword 자체가
    _id라 문서가 하나뿐이다. Qdrant는 payload.keyword로 필터링해 지운다.
    """
    get_collection().delete_many({"keyword": keyword})
    get_collection(CLEANED_COLLECTION).delete_many({"keyword": keyword})
    get_collection(TREND_COLLECTION).delete_many({"keyword": keyword})
    get_collection(CRAWL_REQUESTS_COLLECTION).delete_one({"_id": keyword})
    get_collection(HIDDEN_KEYWORDS_COLLECTION).delete_one({"_id": keyword})
    get_collection(LLM_REQUESTS_COLLECTION).delete_many({"keyword": keyword})
    get_client().delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.Filter(
            must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
        ),
    )


_SCROLL_BATCH_SIZE = 100


def fetch_keyword_chunks(keyword: str) -> list[str]:
    """Qdrant mimori_chunks에서 payload.keyword == keyword인 포인트를 전부 가져와
    payload["text"] 리스트로 반환. 없으면 빈 리스트."""
    ensure_collection()

    scroll_filter = models.Filter(
        must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    )

    client = get_client()
    texts = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=scroll_filter,
            with_payload=True,
            limit=_SCROLL_BATCH_SIZE,
            offset=offset,
        )
        texts.extend(point.payload["text"] for point in points)
        if offset is None:
            break
    return texts


_CHUNK_SEPARATOR = "\n\n---\n\n"

# 크롤링 원문(디시/네이트판 등 비모더레이션 커뮤니티 포함)을 프롬프트에 이어붙이므로,
# 모델이 그 안의 문장을 지시로 착각하지 않도록 명시적 경계로 감싼다. prompt_template.md
# 쪽에도 이 태그 안은 데이터일 뿐이라는 지시문이 짝을 이뤄 존재해야 한다.
_UNTRUSTED_OPEN = "<수집자료>"
_UNTRUSTED_CLOSE = "</수집자료>"


def build_prompt(keyword: str, chunks: list[str], trend_info: str | None = None) -> str:
    """prompt_template.md를 읽어 {keyword}, {content}, {trend_info}를 채운 문자열 반환.

    trend_info를 명시적으로 주면 그대로 쓰고 format_trend_context(실시간 조회)를
    호출하지 않는다. 생략(None)하면 기존과 동일하게 내부에서 조회한다 — API 서버처럼
    캐시된 값을 미리 갖고 있는 호출부는 반드시 trend_info를 넘겨야 실시간 호출이 새지 않는다.

    {content}는 청크 원문을 이어붙인 뒤 프롬프트 인젝션 방지를 위해
    <수집자료>...</수집자료>로 감싼 것.
    """
    with open(ANALYSIS_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    content = f"{_UNTRUSTED_OPEN}\n{_CHUNK_SEPARATOR.join(chunks)}\n{_UNTRUSTED_CLOSE}"
    if trend_info is None:
        trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, content=content, trend_info=trend_info)


# 상태 코드가 메시지에 "[529]"처럼 대괄호로 박혀 나오는 경우, 5xx 전부를 서버 쪽
# 일시적 오류로 간주한다 (503 ResourceExhausted, 529 Overloaded 등 매번 새 코드가
# 나올 수 있어 코드 목록을 하드코딩하는 대신 패턴으로 잡는다).
_RETRYABLE_CODE_PATTERN = re.compile(r"\[(5\d{2})\]")
_RETRYABLE_KEYWORDS = ("ResourceExhausted", "Service Unavailable", "Overloaded")


def _is_retryable(error_message: str) -> bool:
    if _RETRYABLE_CODE_PATTERN.search(error_message):
        return True
    return any(keyword in error_message for keyword in _RETRYABLE_KEYWORDS)


def _log_finish_reason(finish_reason: str | None) -> None:
    """finish_reason이 "length"면 max_completion_tokens 상한에 걸려 답변이
    끊겼다는 뜻이다(모델이 스스로 끝냈으면 "stop"). gpt-oss 계열처럼 답변 전에
    내부 reasoning 토큰을 먼저 쓰는 모델은 이 경우 본문이 아예 비거나 중간에
    잘릴 수 있어, 상한을 실제로 올려야 하는지 판단할 근거로 로그에 남긴다."""
    if finish_reason == "length":
        print("[analyze] ⚠️ finish_reason=length — max_completion_tokens 상한에 걸려 답변이 잘렸습니다")
    elif finish_reason:
        print(f"[analyze] finish_reason={finish_reason}")
    else:
        print("[analyze] finish_reason을 응답에서 못 찾음(response_metadata 없음)")


def invoke_with_retry(nvidia_client, messages, max_retries: int = 3, base_delay: float = 5.0):
    """nvidia_client.invoke()를 호출하되, 5xx(503 ResourceExhausted, 529 Overloaded 등)
    처럼 일시적인 서버 오류면 지수 백오프로 재시도한다. 그 외 오류(인증 실패 등
    재시도해도 소용없는 것)는 바로 던진다. 진행 상황은 print로 출력해 사용자가
    실시간으로 확인할 수 있게 한다."""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[analyze] LLM 호출 시도 {attempt}/{max_retries}...")
            response = nvidia_client.invoke(messages)
            _log_finish_reason(response.response_metadata.get("finish_reason"))
            if attempt > 1:
                print(f"[analyze] 시도 {attempt}에서 성공")
            return response
        except Exception as e:
            retryable = _is_retryable(str(e))
            print(f"[analyze] 시도 {attempt}/{max_retries} 실패: {e}")

            if not retryable:
                print("[analyze] 재시도로 해결될 오류가 아님 — 바로 예외를 던집니다.")
                raise
            if attempt == max_retries:
                print("[analyze] 최대 재시도 횟수 초과 — 예외를 던집니다.")
                raise

            wait = base_delay * (2 ** (attempt - 1))
            print(f"[analyze] 일시적 오류(서버 혼잡)로 판단 — {wait:.0f}초 후 재시도...")
            time.sleep(wait)


def _stream_with_retry(
    nvidia_client, messages, on_chunk: Callable[[str], None], max_retries: int = 3, base_delay: float = 5.0
) -> str:
    """invoke_with_retry의 스트리밍 버전. 청크가 도착할 때마다 지금까지 누적된
    텍스트로 on_chunk를 호출한다. 재시도 시 그때까지 누적된 partial은 버리고
    새 스트림부터 다시 쌓는다(Mongo에 이미 반영된 partial은 다음 청크가 덮어씀)."""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[analyze] LLM 스트리밍 호출 시도 {attempt}/{max_retries}...")
            accumulated = ""
            last_chunk = None
            for chunk in nvidia_client.stream(messages):
                accumulated += chunk.content
                on_chunk(accumulated)
                last_chunk = chunk
            # finish_reason은 스트림의 마지막 청크에만 실려 온다(langchain_nvidia_ai_endpoints
            # 쪽 구현이 그렇게 되어 있음 — 매 청크마다 채우면 응답 메타데이터가 중복 누적됨).
            if last_chunk is not None:
                _log_finish_reason(last_chunk.response_metadata.get("finish_reason"))
            if attempt > 1:
                print(f"[analyze] 시도 {attempt}에서 성공")
            return accumulated
        except Exception as e:
            retryable = _is_retryable(str(e))
            print(f"[analyze] 스트리밍 시도 {attempt}/{max_retries} 실패: {e}")

            if not retryable:
                print("[analyze] 재시도로 해결될 오류가 아님 — 바로 예외를 던집니다.")
                raise
            if attempt == max_retries:
                print("[analyze] 최대 재시도 횟수 초과 — 예외를 던집니다.")
                raise

            wait = base_delay * (2 ** (attempt - 1))
            print(f"[analyze] 일시적 오류(서버 혼잡)로 판단 — {wait:.0f}초 후 재시도...")
            time.sleep(wait)


def analyze(prompt: str, model: str = ANALYSIS_MODEL, on_chunk: Callable[[str], None] | None = None) -> str:
    """prompt를 model에 보내 분석 결과 텍스트를 반환. 503 등 일시적 오류는 자동 재시도.

    on_chunk가 주어지면 .stream()으로 호출해 청크가 도착할 때마다 누적 텍스트로
    on_chunk를 부른다(호출자가 진행 상황을 어딘가에 반영하고 싶을 때 사용, 예:
    scripts/llm_request_worker.py가 Mongo에 partial_text를 스로틀 기록). 생략하면
    기존과 동일하게 완료까지 블로킹하는 invoke_with_retry를 쓴다."""
    nvidia_client = ChatNVIDIA(
        model=model,
        api_key=NIM_KEY,
        temperature=0,
        top_p=0.95,
        max_completion_tokens=4096,
        timeout=6000
    )
    messages = [{"role": "user", "content": prompt}]

    if on_chunk is None:
        response = invoke_with_retry(nvidia_client, messages)
        return response.content

    return _stream_with_retry(nvidia_client, messages, on_chunk)

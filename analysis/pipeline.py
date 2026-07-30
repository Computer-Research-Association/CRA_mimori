"""
analysis/pipeline.py
이미 임베딩된 밈 키워드 목록 조회, Qdrant 청크 조회, 프롬프트 조립, LLM 분석 호출.
"""

import time

import ollama
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from qdrant_client.http import models

from config.config_cilent import ANALYSIS_MODEL, ANALYSIS_PROMPT_PATH, NIM_KEY, QDRANT_COLLECTION
from DB.drant_clitent import client, ensure_collection
from DB.mongo_client import get_collection
from trend.trend_service import format_trend_context


def list_analyzable_keywords() -> list[str]:
    """memes 컬렉션에서 is_embedded=True인 문서들의 distinct keyword 목록 반환 (정렬됨)."""
    collection = get_collection()
    keywords = collection.distinct("keyword", {"is_embedded": True})
    return sorted(keywords)


_SCROLL_BATCH_SIZE = 100


def fetch_keyword_chunks(keyword: str) -> list[str]:
    """Qdrant mimori_chunks에서 payload.keyword == keyword인 포인트를 전부 가져와
    payload["text"] 리스트로 반환. 없으면 빈 리스트."""
    ensure_collection()

    scroll_filter = models.Filter(
        must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    )

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


def build_prompt(keyword: str, chunks: list[str]) -> str:
    """prompt_template.md를 읽어 {keyword}, {content}, {trend_info}를 채운 문자열 반환."""
    with open(ANALYSIS_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    content = _CHUNK_SEPARATOR.join(chunks)
    trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, content=content, trend_info=trend_info)


_RETRYABLE_SIGNALS = ("503", "ResourceExhausted", "Service Unavailable")


def invoke_with_retry(nvidia_client, messages, max_retries: int = 3, base_delay: float = 5.0):
    """nvidia_client.invoke()를 호출하되, 503/ResourceExhausted처럼 일시적인 오류면
    지수 백오프로 재시도한다. 그 외 오류(인증 실패 등 재시도해도 소용없는 것)는 바로 던진다.
    진행 상황은 print로 출력해 사용자가 실시간으로 확인할 수 있게 한다."""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[analyze] LLM 호출 시도 {attempt}/{max_retries}...")
            response = nvidia_client.invoke(messages)
            if attempt > 1:
                print(f"[analyze] 시도 {attempt}에서 성공")
            return response
        except Exception as e:
            retryable = any(signal in str(e) for signal in _RETRYABLE_SIGNALS)
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


def analyze(prompt: str, model: str = ANALYSIS_MODEL) -> str:
    """prompt를 model에 보내 분석 결과 텍스트를 반환. 503 등 일시적 오류는 자동 재시도."""
    nvidia_client = ChatNVIDIA(
        model=model,
        api_key=NIM_KEY,
        temperature=1,
        top_p=0.95,
        max_completion_tokens=16384,
        timeout=6000
    )

    response = invoke_with_retry(nvidia_client, [{"role": "user", "content": prompt}])
    return response.content

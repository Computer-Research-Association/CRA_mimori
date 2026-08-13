"""
llm_request_worker.py
llm_requests 큐에서 가장 오래된 queued 요청 하나를 집어서 analyze 또는 rag를
처리한다. scheduler가 5초마다 이 스크립트를 서브프로세스로 실행한다(BGE-M3 등
무거운 의존성을 프로세스 종료와 함께 OS가 회수하게 하려고 — crawl_request_worker와
동일한 이유).

크롤 워커와 BGE-M3를 동시에 메모리에 올리지 않도록 heavy_job_lock을 처리 구간
전체(임베딩~LLM 호출)에 건다. 락을 못 잡으면 이번 틱은 포기하고 문서를
queued로 되돌려 다음 틱에 재시도한다(사전에 아무 무거운 작업도 안 했으므로
sunk cost 없음).

큐가 비어있으면 아무 일도 하지 않고 조용히 종료한다(다음 틱에 재시도).
동시 처리 1개 제한은 scheduler.py의 APScheduler max_instances(기본값 1)가 보장한다.
"""
import os
import sys

# torch와 다른 네이티브 의존성(qdrant-client/ollama 등)이 각자 별도의 Intel
# OpenMP 런타임(libiomp5md.dll)을 들고 있어, 한 프로세스에서 같이 로드되면
# Windows에서 세그폴트가 난다(rag_main.py와 동일한 이유로 여기도 필요).
# import torch가 일어나기 전에 반드시 설정해야 함.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 아래 임포트 순서는 우연이 아니다 — pymongo(DB.mongo_client/config.config_cilent)를
# analysis.pipeline보다 먼저 최상위에서 임포트하면(둘 다 결국 같은 모듈을 로드하는데도)
# 네이티브 라이브러리 초기화 순서가 꼬여 위와 같은 세그폴트가 재현된다. 반드시
# analysis.pipeline/rag_pipeline/embedding.encoder를 먼저 임포트한 뒤에
# DB.mongo_client/config.config_cilent를 임포트할 것.
from analysis.pipeline import analyze
from analysis.query import build_search_query
from analysis.rag_pipeline import (
    build_facet_prompt,
    build_rag_prompt,
    clean_source_url,
    default_facet_config,
    encode_facets,
    facet_search,
    search_relevant_chunks,
)
from embedding.encoder import encode_batch
from DB.mongo_client import get_collection
from config.config_cilent import LLM_REQUESTS_COLLECTION
from scripts.heavy_job_lock import acquire_heavy_job_lock, release_heavy_job_lock
from trend.trend_service import format_trend_context, get_cached_trend

_LOCK_OWNER = "llm_request_worker"


def _mark_done(collection, job_id: str, result: dict) -> None:
    collection.update_one(
        {"_id": job_id},
        {"$set": {"status": "done", "completed_at": datetime.now(timezone.utc), "result": result}},
    )


def _mark_failed(collection, job_id: str, error: str) -> None:
    collection.update_one(
        {"_id": job_id},
        {"$set": {"status": "failed", "completed_at": datetime.now(timezone.utc), "error": error}},
    )


def _requeue_stale_running(collection, stale_after: timedelta = timedelta(minutes=20)) -> None:
    """max_instances=1이 동시 실행은 막아주지만, 프로세스가 처리 도중 죽으면(OOM 등)
    Mongo 문서만 running에 멈춰 남는다. 다음 실행 때 그런 고아 상태를 회수한다."""
    cutoff = datetime.now(timezone.utc) - stale_after
    collection.update_many(
        {"status": "running", "started_at": {"$lt": cutoff}},
        {"$set": {"status": "queued"}},
    )


def _trend_info(keyword: str) -> tuple[str, dict | None]:
    cached_trend = get_cached_trend(keyword)
    if not cached_trend:
        return "", None
    try:
        trend_info = format_trend_context(keyword, result=cached_trend)
    except Exception:
        trend_info = ""
    trend_response = {k: v for k, v in cached_trend.items() if k != "_id"}
    return trend_info, trend_response


def _process_analyze(doc: dict) -> dict:
    keyword = doc["keyword"]
    facet_config = default_facet_config(keyword)
    facet_vectors = encode_facets(facet_config)
    points, _ = facet_search(keyword, facet_config, facet_vectors=facet_vectors, is_relevant=True)
    if not points:
        raise ValueError(f"'{keyword}' 데이터를 찾을 수 없습니다")

    trend_info, trend_response = _trend_info(keyword)
    prompt = build_facet_prompt(keyword, points, trend_info=trend_info)
    result_text = analyze(prompt)

    sources = [
        {"title": p.payload.get("title") or "제목 없음", "url": clean_source_url(p.payload.get("url"))}
        for p in points
    ]
    return {"result": result_text, "sources": sources, "trend": trend_response}


def _process_rag(doc: dict) -> dict:
    keyword = doc["keyword"]
    question = doc["question"]
    sources_filter = doc.get("sources")

    search_query = build_search_query(keyword, question)
    dense_vecs, lexical_weights = encode_batch([search_query])
    points = search_relevant_chunks(
        keyword, dense_vecs[0], lexical_weights[0], sources=sources_filter, is_relevant=True
    )
    if not points:
        raise ValueError(f"'{keyword}'에 대한 검색 결과가 없습니다")

    trend_info, trend_response = _trend_info(keyword)
    prompt = build_rag_prompt(keyword, question, points, trend_info=trend_info)
    answer = analyze(prompt)

    sources = [
        {"title": p.payload.get("title") or "제목 없음", "url": clean_source_url(p.payload.get("url"))}
        for p in points
    ]
    return {"answer": answer, "sources": sources, "trend": trend_response}


def run_once(collection=None) -> None:
    if collection is None:
        collection = get_collection(LLM_REQUESTS_COLLECTION)

    _requeue_stale_running(collection)

    doc = collection.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
        sort=[("requested_at", 1)],
    )
    if doc is None:
        return

    job_id = doc["_id"]

    if not acquire_heavy_job_lock(_LOCK_OWNER):
        collection.update_one({"_id": job_id}, {"$set": {"status": "queued", "started_at": None}})
        return

    try:
        if doc["kind"] == "analyze":
            result = _process_analyze(doc)
        else:
            result = _process_rag(doc)
        _mark_done(collection, job_id, result)
    except Exception as e:
        _mark_failed(collection, job_id, str(e))
    finally:
        release_heavy_job_lock(_LOCK_OWNER)


if __name__ == "__main__":
    run_once()

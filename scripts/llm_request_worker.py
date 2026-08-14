"""
llm_request_worker.py
llm_requests 큐에서 가장 오래된 queued 요청 하나를 집어서 analyze를 처리한다.

단발 실행(run_once)과 상주 루프(run_loop) 두 진입점이 있다. scheduler.py는
큐에 처리할 게 있는데 상주 워커가 안 떠 있을 때만 `--loop`로 이 스크립트를
새 프로세스로 띄운다 — 매 요청마다 새로 띄우면 BGE-M3 로드(수십 초)를 매번
반복해서 실제 처리 시간보다 콜드스타트가 훨씬 커지기 때문이다(2026-08-14,
이미 크롤된 키워드도 매번 수십 초씩 걸린다는 리포트로 발견). run_loop는 큐가
LLM_WORKER_IDLE_TIMEOUT_SECONDS만큼 비면 스스로 종료해 BGE-M3가 유휴 상태로
메모리를 무한정 점유하지 않게 한다 — 다음 요청이 오면 scheduler.py가 새
프로세스를 다시 띄우고, 그 첫 요청만 콜드스타트를 다시 겪는다.

크롤 워커와 BGE-M3를 동시에 메모리에 올리지 않도록 heavy_job_lock을 처리 구간
전체(임베딩~LLM 호출)에 건다. 락을 못 잡으면 이번 시도는 포기하고 문서를
queued로 되돌려 다음 시도에 재시도한다(사전에 아무 무거운 작업도 안 했으므로
sunk cost 없음). 락은 요청을 실제로 처리하는 구간에만 걸리므로, run_loop가
유휴 대기 중인 동안은 크롤 워커를 막지 않는다.

동시 처리 1개 제한은 scheduler.py의 APScheduler max_instances(기본값 1) +
"이미 상주 워커가 떠 있으면 새로 안 띄움" 체크가 함께 보장한다.
"""
import os
import sys
import time

# torch와 다른 네이티브 의존성(qdrant-client/ollama 등)이 각자 별도의 Intel
# OpenMP 런타임(libiomp5md.dll)을 들고 있어, 한 프로세스에서 같이 로드되면
# Windows에서 세그폴트가 난다(rag_main.py와 동일한 이유로 여기도 필요).
# import torch가 일어나기 전에 반드시 설정해야 함.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 아래 임포트 순서는 우연이 아니다 — pymongo(DB.mongo_client/config.config_cilent)를
# analysis.pipeline/rag_pipeline보다 먼저 최상위에서 임포트하면(rag_pipeline이
# encode_facets 안에서 지연 임포트하는 embedding.encoder가 결국 torch를 끌고 오는데,
# 이게 pymongo보다 나중에 로드되면) 네이티브 라이브러리 초기화 순서가 꼬여 위와
# 같은 세그폴트가 재현된다. 반드시 analysis.pipeline/rag_pipeline을 먼저 임포트한
# 뒤에 DB.mongo_client/config.config_cilent를 임포트할 것.
from analysis.pipeline import analyze
from analysis.rag_pipeline import (
    build_facet_prompt,
    clean_source_url,
    default_facet_config,
    encode_facets,
    facet_search,
)
from DB.mongo_client import get_collection
from config.config_cilent import (
    LLM_REQUESTS_COLLECTION,
    LLM_WORKER_IDLE_TIMEOUT_SECONDS,
    LLM_WORKER_POLL_INTERVAL_SECONDS,
)
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
    sources_filter = doc.get("sources")
    facet_config = default_facet_config(keyword)
    facet_vectors = encode_facets(facet_config)
    points, _ = facet_search(
        keyword, facet_config, facet_vectors=facet_vectors, sources=sources_filter, is_relevant=True
    )
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


def run_once(collection=None) -> bool:
    """대기 중인 요청 하나를 처리한다.

    반환값은 run_loop의 idle-timeout 판단에 쓰인다 — 큐가 정말 비어 있었으면
    False, 그 외(처리 완료·실패·락 경합으로 재시도 예약 등 뭔가 할 일이
    있었으면)는 True. 락 경합도 True인 이유: 빈 큐가 아니라 크롤 워커와
    부딪힌 것뿐이라 '유휴'로 치면 안 되기 때문이다.
    """
    if collection is None:
        collection = get_collection(LLM_REQUESTS_COLLECTION)

    _requeue_stale_running(collection)

    doc = collection.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
        sort=[("requested_at", 1)],
    )
    if doc is None:
        return False

    job_id = doc["_id"]

    if not acquire_heavy_job_lock(_LOCK_OWNER):
        collection.update_one({"_id": job_id}, {"$set": {"status": "queued", "started_at": None}})
        return True

    try:
        result = _process_analyze(doc)
        _mark_done(collection, job_id, result)
    except Exception as e:
        _mark_failed(collection, job_id, str(e))
    finally:
        release_heavy_job_lock(_LOCK_OWNER)
    return True


def run_loop(idle_timeout: float = LLM_WORKER_IDLE_TIMEOUT_SECONDS) -> None:
    """큐가 idle_timeout초 동안 계속 비어 있을 때까지 run_once를 반복한다.

    BGE-M3(embedding/encoder.py의 모듈 전역 싱글턴)가 프로세스 생존 기간
    내내 재사용되므로, 이 루프 안에서 이어지는 요청들은 모델을 다시 로드하지
    않는다. 프로세스가 죽지 않고 계속 대기하는 동안은 크롤 워커에게 넘길
    heavy_job_lock을 붙잡지 않는다(run_once가 처리 구간에만 짧게 건다).
    """
    collection = get_collection(LLM_REQUESTS_COLLECTION)
    idle_since = time.monotonic()
    while True:
        did_work = run_once(collection=collection)
        now = time.monotonic()
        if did_work:
            idle_since = now
        elif now - idle_since >= idle_timeout:
            break
        time.sleep(LLM_WORKER_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        run_loop()
    else:
        run_once()

"""
llm_request_worker.py
llm_requests 큐에서 가장 오래된 queued 요청 하나를 집어서 analyze를 처리한다.

단발 실행(run_once)과 상주 루프(run_loop) 두 진입점이 있다. scheduler.py는
컨테이너가 뜰 때 이 스크립트를 `--loop`로 딱 한 번 서브프로세스로 띄워
컨테이너 수명 내내 상주시킨다 — 매 요청마다 새로 띄우면 BGE-M3 로드(수십 초)를
매번 반복해서 실제 처리 시간보다 콜드스타트가 훨씬 커지기 때문이다(2026-08-14,
이미 크롤된 키워드도 매번 수십 초씩 걸린다는 리포트로 발견). run_loop는 큐가
비어도 종료하지 않고 계속 폴링한다 — 유휴 시에도 BGE-M3가 RAM 2~3GB를 계속
쓰지만, EC2 free -h로 확인한 가용 메모리(6.3GB)가 그 정도는 감당하길래 콜드
스타트를 완전히 없애는 쪽을 택했다. scheduler.py는 이 프로세스가 죽으면(OOM
등) watchdog으로 재기동만 한다.

크롤 워커와 BGE-M3를 동시에 메모리에 올리지 않도록 heavy_job_lock을 처리 구간
전체(임베딩~LLM 호출)에 건다. 락을 못 잡으면 이번 시도는 포기하고 문서를
queued로 되돌려 다음 시도에 재시도한다(사전에 아무 무거운 작업도 안 했으므로
sunk cost 없음). 락은 요청을 실제로 처리하는 구간에만 걸리므로, run_loop가
유휴 대기 중인 동안은 크롤 워커를 막지 않는다.

동시 처리 1개 제한은 scheduler.py의 APScheduler max_instances(기본값 1) +
상주 워커가 하나뿐이라는 사실이 함께 보장한다.
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
    ANALYZE_STREAM_WRITE_INTERVAL_SECONDS,
    LLM_REQUESTS_COLLECTION,
    LLM_WORKER_POLL_INTERVAL_SECONDS,
    MIN_COMMUNITY_DOCS_FOR_TAVILY,
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


def _write_progress(
    collection, job_id: str, sources: list, trend: dict | None, partial_text: str | None,
    low_confidence: bool = False,
) -> None:
    """LLM이 아직 답변 중이어도 프론트가 볼 수 있게 result 필드를 미리 채워둔다.
    _mark_done과 동일하게 dotted-path 대신 result 전체를 매번 덮어쓴다. 쓰기 실패는
    (crawl_request_worker의 progress 콜백과 동일하게) 삼켜서 작업 자체를 막지 않는다
    — 마지막 상태는 _mark_done이 최종 결과로 다시 덮어쓰므로 유실돼도 무해하다."""
    try:
        collection.update_one(
            {"_id": job_id},
            {"$set": {"result": {
                "sources": sources, "trend": trend, "partial_text": partial_text,
                "low_confidence": low_confidence,
            }}},
        )
    except Exception as e:
        print(f"[llm_request_worker] 진행 상황 기록 실패(무시하고 계속): {e}")


def _make_stream_progress_writer(collection, job_id: str, sources: list, trend: dict | None, low_confidence: bool):
    """analyze(on_chunk=...)에 넘길 콜백. ANALYZE_STREAM_WRITE_INTERVAL_SECONDS보다
    짧은 간격의 청크는 건너뛰어 Mongo write가 토큰 속도로 발생하지 않게 한다."""
    last_write = 0.0

    def on_chunk(accumulated_text: str) -> None:
        nonlocal last_write
        now = time.monotonic()
        if now - last_write < ANALYZE_STREAM_WRITE_INTERVAL_SECONDS:
            return
        last_write = now
        _write_progress(collection, job_id, sources, trend, accumulated_text, low_confidence)

    return on_chunk


def _process_analyze(doc: dict, collection, job_id: str) -> dict:
    keyword = doc["keyword"]
    sources_filter = doc.get("sources")
    facet_config = default_facet_config(keyword)
    facet_vectors = encode_facets(facet_config)
    points, _ = facet_search(
        keyword, facet_config, facet_vectors=facet_vectors, sources=sources_filter, is_relevant=True
    )
    if not points:
        # 이 시점의 keyword는 이미 api/routes.py에서 list_analyzable_keywords()로
        # 존재를 확인받은 뒤라, 여기서 못 찾는 건 "키워드 자체가 없음"이 아니라
        # "선택한 출처 조합에서만 자료가 없음"이다. 메시지를 그렇게 구분해야
        # 사용자가 "이 밈은 없구나"로 오해하지 않고 출처를 넓혀보게 된다.
        if sources_filter:
            raise ValueError(f"선택하신 출처에는 '{keyword}' 관련 자료가 없습니다. 출처를 더 선택해보세요.")
        raise ValueError(f"'{keyword}' 관련 자료를 찾을 수 없습니다")

    trend_info, trend_response = _trend_info(keyword)
    prompt = build_facet_prompt(keyword, points, trend_info=trend_info)

    sources = [
        {"title": p.payload.get("title") or "제목 없음", "url": clean_source_url(p.payload.get("url"))}
        for p in points
    ]
    # 근거로 쓴 서로 다른 출처(URL) 수가 Keywords.md 승격 문턱과 같은 기준(3건) 미만이면
    # "확신도 낮음"으로 표시한다 — 청크 개수가 아니라 distinct URL 개수를 본다. 같은 글이
    # facet(의미/유행_이유/...)마다 중복으로 뽑혀도 실제 근거 문서 수는 하나이기 때문이다.
    # 실사례("ㅈㄱㄴ"): 자료가 몇 안 되는 특정 커뮤니티의 국지적 용법을, 훨씬 널리 쓰이는
    # 뜻인 것처럼 확신에 차서 답한 적이 있었다 — 근거가 얕을 땐 그렇게 안 보이게 한다.
    distinct_source_count = len({s["url"] for s in sources})
    low_confidence = distinct_source_count < MIN_COMMUNITY_DOCS_FOR_TAVILY

    # 출처/트렌드는 LLM 호출 전에 이미 다 계산됐다 — done을 기다리지 않고 먼저 노출한다.
    _write_progress(collection, job_id, sources, trend_response, partial_text=None, low_confidence=low_confidence)

    on_chunk = _make_stream_progress_writer(collection, job_id, sources, trend_response, low_confidence)
    result_text = analyze(prompt, on_chunk=on_chunk)

    return {"result": result_text, "sources": sources, "trend": trend_response, "low_confidence": low_confidence}


def run_once(collection=None) -> bool:
    """대기 중인 요청 하나를 처리한다. 처리할 게 있었으면(완료·실패·락 경합으로
    재시도 예약 포함) True, 큐가 비어 있었으면 False."""
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
        result = _process_analyze(doc, collection, job_id)
        _mark_done(collection, job_id, result)
    except Exception as e:
        _mark_failed(collection, job_id, str(e))
    finally:
        release_heavy_job_lock(_LOCK_OWNER)
    return True


def run_loop() -> None:
    """종료하지 않고 큐를 계속 폴링한다 — scheduler.py가 컨테이너 기동 시
    한 번만 띄워 상주시킨다.

    BGE-M3(embedding/encoder.py의 모듈 전역 싱글턴)가 프로세스 생존 기간
    내내 재사용되므로, 이 루프 안에서 이어지는 요청들은 모델을 다시 로드하지
    않는다. 큐가 비어 대기하는 동안은 heavy_job_lock을 붙잡지 않으므로
    (run_once가 처리 구간에만 짧게 건다) 크롤 워커를 막지 않는다.
    """
    collection = get_collection(LLM_REQUESTS_COLLECTION)
    while True:
        run_once(collection=collection)
        time.sleep(LLM_WORKER_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        run_loop()
    else:
        run_once()

"""
crawl_request_worker.py
crawl_requests 큐에서 가장 오래된 queued 요청 하나를 집어서 크롤링→정제→임베딩까지
이어서 처리한다. scheduler가 1분마다 이 스크립트를 서브프로세스로 실행한다
(BGE-M3 등 무거운 의존성을 프로세스 종료와 함께 OS가 회수하게 하려고 —
기존 crawlrun/preprocess_embed_run과 동일한 이유).

큐가 비어있으면 아무 일도 하지 않고 조용히 종료한다(다음 틱에 재시도).
동시 처리 1개 제한은 이 스크립트가 아니라 scheduler.py의 APScheduler
max_instances(기본값 1)가 보장한다 — 같은 job이 아직 안 끝났으면 다음 틱을 건너뛴다.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DB.mongo_client import get_collection
from config.config_cilent import CRAWL_REQUESTS_COLLECTION
from main import crawl_all
from preprocessing.pipeline import preprocess_documents
from embedding.pipeline import embed_documents


def _mark(collection, keyword: str, status: str, error: str | None = None) -> None:
    update = {"status": status, "completed_at": datetime.now(timezone.utc)}
    if error is not None:
        update["error"] = error
    collection.update_one({"_id": keyword}, {"$set": update})


def _requeue_stale_running(collection, stale_after: timedelta = timedelta(hours=2)) -> None:
    """max_instances=1이 동시 실행은 막아주지만, 프로세스가 크롤링 도중 죽으면(OOM 등)
    Mongo 문서만 running에 멈춰 남는다. 이 함수는 그런 고아 상태를 다음 실행 때 회수한다."""
    cutoff = datetime.now(timezone.utc) - stale_after
    collection.update_many(
        {"status": "running", "started_at": {"$lt": cutoff}},
        {"$set": {"status": "queued"}},
    )


def run_once(collection=None) -> None:
    """큐에서 가장 오래된 queued 요청 하나를 처리. 없으면 즉시 반환."""
    if collection is None:
        collection = get_collection(CRAWL_REQUESTS_COLLECTION)

    _requeue_stale_running(collection)

    doc = collection.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
        sort=[("requested_at", 1)],
    )
    if doc is None:
        return

    keyword = doc["_id"]
    try:
        crawl_all([keyword])
        preprocess_documents(keyword)
        embed_result = embed_documents(keyword)
        if embed_result.get("documents", 0) == 0:
            _mark(collection, keyword, "failed", error="수집된 데이터가 없습니다 (모든 소스에서 관련 자료를 찾지 못했습니다)")
        else:
            _mark(collection, keyword, "done")
    except Exception as e:
        _mark(collection, keyword, "failed", error=str(e))


if __name__ == "__main__":
    run_once()

"""
heavy_job_lock.py
crawl_request_worker와 llm_request_worker가 서로 다른 서브프로세스로 동시에 떠서
BGE-M3(수GB)를 동시에 메모리에 올리는 걸 막는 Mongo 기반 락.

BGE-M3가 실제로 메모리에 있는 구간(임베딩~LLM 호출 전후)만 감싸야 한다 —
크롤링처럼 네트워크만 기다리는 구간까지 감싸면 analyze/rag 요청이 크롤이
끝날 때까지 수십 분씩 밀릴 수 있다.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DB.mongo_client import get_collection
from config.config_cilent import LOCKS_COLLECTION

_LOCK_ID = "heavy_job_lock"
_STALE_AFTER = timedelta(minutes=20)


def _collection():
    return get_collection(LOCKS_COLLECTION)


def acquire_heavy_job_lock(owner: str, collection=None) -> bool:
    """한 번 시도해서 락을 잡으면 True. 이미 다른 owner가 잡고 있고 스테일이 아니면 False."""
    if collection is None:
        collection = _collection()

    collection.update_one(
        {"_id": _LOCK_ID},
        {"$setOnInsert": {"locked": False, "owner": None, "locked_at": None}},
        upsert=True,
    )

    doc = collection.find_one({"_id": _LOCK_ID})
    now = datetime.now(timezone.utc)
    stale = bool(doc["locked"] and doc["locked_at"] and (now - doc["locked_at"]) > _STALE_AFTER)
    if doc["locked"] and not stale:
        return False

    # doc을 읽은 시점의 locked_at을 필터에 넣어, 그 사이 다른 프로세스가 먼저
    # 채갔으면(locked_at이 바뀌었으면) 이 update가 실패하게 한다(원자적 획득).
    result = collection.update_one(
        {"_id": _LOCK_ID, "locked_at": doc["locked_at"]},
        {"$set": {"locked": True, "owner": owner, "locked_at": now}},
    )
    return result.modified_count == 1


def release_heavy_job_lock(owner: str, collection=None) -> None:
    """owner가 실제 소유자일 때만 락을 푼다."""
    if collection is None:
        collection = _collection()
    collection.update_one(
        {"_id": _LOCK_ID, "owner": owner},
        {"$set": {"locked": False, "owner": None}},
    )


def acquire_heavy_job_lock_blocking(
    owner: str,
    timeout: timedelta = timedelta(minutes=10),
    poll_interval: float = 5.0,
    collection=None,
) -> bool:
    """timeout 안에 락을 잡을 때까지 poll_interval초 간격으로 재시도.
    성공하면 True, 시간 초과면 False."""
    if collection is None:
        collection = _collection()
    deadline = time.monotonic() + timeout.total_seconds()
    while True:
        if acquire_heavy_job_lock(owner, collection=collection):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)

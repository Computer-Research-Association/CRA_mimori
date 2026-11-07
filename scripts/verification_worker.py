"""새 임베딩 근거가 기존 분석을 다시 만들 만큼 충분한지 보수적으로 판단한다."""
from datetime import datetime, timezone
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DB.mongo_client import get_collection
from config.config_cilent import EVIDENCE_VERIFICATION_COLLECTION, LLM_REQUESTS_COLLECTION

MIN_NEW_DOCUMENTS = 2
MIN_NEW_SOURCES = 2


def run_once(collection=None, llm_requests_collection=None) -> bool:
    # PyMongo Collection은 bool()을 허용하지 않으므로 명시적으로 None만 검사한다.
    if collection is None:
        collection = get_collection(EVIDENCE_VERIFICATION_COLLECTION)
    if llm_requests_collection is None:
        llm_requests_collection = get_collection(LLM_REQUESTS_COLLECTION)
    candidate = collection.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
        sort=[("requested_at", 1)],
    )
    if candidate is None:
        return False

    now = datetime.now(timezone.utc)
    keyword = candidate["keyword"]
    material = (
        candidate.get("document_count", 0) >= MIN_NEW_DOCUMENTS
        and candidate.get("source_count", 0) >= MIN_NEW_SOURCES
    )
    if material:
        llm_requests_collection.update_many(
            {"keyword": keyword, "status": "done"},
            {"$set": {
                "status": "queued", "requested_at": now, "started_at": None,
                "completed_at": None, "error": None, "result": None,
                "refresh_reason": "evidence",
            }},
        )
        outcome = "requeued"
    else:
        llm_requests_collection.update_many(
            {"keyword": keyword, "status": "done"},
            {"$set": {"verification": {"status": "unchanged", "checked_at": now}}},
        )
        outcome = "unchanged"
    collection.update_one(
        {"_id": candidate["_id"]},
        {"$set": {"status": "done", "completed_at": now, "outcome": outcome}},
    )
    return True


if __name__ == "__main__":
    run_once()

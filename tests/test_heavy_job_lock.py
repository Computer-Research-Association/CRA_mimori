"""
heavy_job_lock.py의 acquire/release/blocking-acquire 단위 테스트.
실제 Mongo 없이 fake collection으로 검증.

실행: uv run python tests/test_heavy_job_lock.py
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.heavy_job_lock as lock_module
from scripts.heavy_job_lock import (
    acquire_heavy_job_lock,
    acquire_heavy_job_lock_blocking,
    release_heavy_job_lock,
)


class _FakeLocksCollection:
    """find_one / update_one(upsert 포함)만 흉내낸다."""

    def __init__(self):
        self._docs = {}

    def find_one(self, filter_):
        return self._docs.get(filter_.get("_id"))

    def update_one(self, filter_, update, upsert=False):
        doc_id = filter_.get("_id")
        doc = self._docs.get(doc_id)
        if doc is None:
            if not upsert:
                return SimpleNamespace(modified_count=0)
            new_doc = {"_id": doc_id}
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            self._docs[doc_id] = new_doc
            return SimpleNamespace(modified_count=0)
        for key, expected in filter_.items():
            if key == "_id":
                continue
            if doc.get(key) != expected:
                return SimpleNamespace(modified_count=0)
        if "$set" in update:
            doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)


def test_처음_획득은_성공한다():
    collection = _FakeLocksCollection()
    assert acquire_heavy_job_lock("워커A", collection=collection) is True
    doc = collection._docs["heavy_job_lock"]
    assert doc["locked"] is True, doc
    assert doc["owner"] == "워커A", doc
    print("[OK] 처음 획득 성공")


def test_이미_잠겨있으면_실패한다():
    collection = _FakeLocksCollection()
    assert acquire_heavy_job_lock("워커A", collection=collection) is True
    assert acquire_heavy_job_lock("워커B", collection=collection) is False
    doc = collection._docs["heavy_job_lock"]
    assert doc["owner"] == "워커A", "워커B가 뺏으면 안 됨"
    print("[OK] 이미 잠겨있으면 다른 owner는 획득 실패")


def test_해제후_다시_획득할_수_있다():
    collection = _FakeLocksCollection()
    acquire_heavy_job_lock("워커A", collection=collection)
    release_heavy_job_lock("워커A", collection=collection)
    doc = collection._docs["heavy_job_lock"]
    assert doc["locked"] is False, doc
    assert acquire_heavy_job_lock("워커B", collection=collection) is True
    print("[OK] 해제 후 다른 owner가 획득 가능")


def test_소유자가_아니면_해제되지_않는다():
    collection = _FakeLocksCollection()
    acquire_heavy_job_lock("워커A", collection=collection)
    release_heavy_job_lock("워커B", collection=collection)  # 워커A 소유인데 워커B가 해제 시도
    doc = collection._docs["heavy_job_lock"]
    assert doc["locked"] is True, "소유자가 아닌 워커가 해제해서는 안 됨"
    print("[OK] 소유자가 아니면 release가 무시됨")


def test_스테일_락은_회수된다():
    collection = _FakeLocksCollection()
    collection._docs["heavy_job_lock"] = {
        "_id": "heavy_job_lock", "locked": True, "owner": "죽은워커",
        "locked_at": datetime.now(timezone.utc) - timedelta(minutes=25),
    }
    assert acquire_heavy_job_lock("워커B", collection=collection) is True
    doc = collection._docs["heavy_job_lock"]
    assert doc["owner"] == "워커B", doc
    print("[OK] 20분 넘은 락은 다른 owner가 회수 가능")


def test_20분_이내_락은_회수되지_않는다():
    collection = _FakeLocksCollection()
    collection._docs["heavy_job_lock"] = {
        "_id": "heavy_job_lock", "locked": True, "owner": "진행중워커",
        "locked_at": datetime.now(timezone.utc) - timedelta(minutes=5),
    }
    assert acquire_heavy_job_lock("워커B", collection=collection) is False
    print("[OK] 20분 이내 락은 회수되지 않음")


def test_blocking_획득은_풀릴때까지_기다렸다가_성공한다():
    collection = _FakeLocksCollection()
    acquire_heavy_job_lock("워커A", collection=collection)

    def _release_later():
        time.sleep(0.2)
        release_heavy_job_lock("워커A", collection=collection)

    import threading
    threading.Thread(target=_release_later).start()

    start = time.monotonic()
    ok = acquire_heavy_job_lock_blocking(
        "워커B", timeout=timedelta(seconds=2), poll_interval=0.05, collection=collection,
    )
    elapsed = time.monotonic() - start
    assert ok is True, "0.2초 뒤 풀렸으니 2초 타임아웃 안에 성공해야 함"
    assert elapsed < 1.0, f"너무 오래 걸림: {elapsed:.2f}s"
    print(f"[OK] blocking 획득 성공 (elapsed={elapsed:.2f}s)")


def test_blocking_획득은_타임아웃되면_False를_반환한다():
    collection = _FakeLocksCollection()
    acquire_heavy_job_lock("워커A", collection=collection)  # 계속 안 풀림

    start = time.monotonic()
    ok = acquire_heavy_job_lock_blocking(
        "워커B", timeout=timedelta(milliseconds=150), poll_interval=0.05, collection=collection,
    )
    elapsed = time.monotonic() - start
    assert ok is False, "안 풀렸으니 실패해야 함"
    assert elapsed < 1.0, f"타임아웃보다 훨씬 오래 걸림: {elapsed:.2f}s"
    print(f"[OK] blocking 획득 타임아웃 (elapsed={elapsed:.2f}s)")


if __name__ == "__main__":
    test_처음_획득은_성공한다()
    test_이미_잠겨있으면_실패한다()
    test_해제후_다시_획득할_수_있다()
    test_소유자가_아니면_해제되지_않는다()
    test_스테일_락은_회수된다()
    test_20분_이내_락은_회수되지_않는다()
    test_blocking_획득은_풀릴때까지_기다렸다가_성공한다()
    test_blocking_획득은_타임아웃되면_False를_반환한다()
    print("\nALL PASS")

"""
crawl_request_worker.run_once() 단위 테스트.
실제 Mongo/크롤러/임베딩 없이 fake collection + monkeypatch로 오케스트레이션만 검증.

실행: uv run python tests/test_crawl_request_worker.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.crawl_request_worker as worker


class _FakeCollection:
    """find_one_and_update와 update_one만 흉내낸다 (run_once가 쓰는 두 가지)."""

    def __init__(self, docs):
        self._docs = {d["_id"]: d for d in docs}

    def find_one_and_update(self, filter_, update, sort=None):
        candidates = [d for d in self._docs.values() if d["status"] == filter_["status"]]
        if not candidates:
            return None
        candidates.sort(key=lambda d: d["requested_at"])
        doc = candidates[0]
        doc.update(update["$set"])
        return doc

    def update_one(self, filter_, update):
        doc = self._docs[filter_["_id"]]
        doc.update(update["$set"])


def test_큐가_비어있으면_아무것도_안한다():
    collection = _FakeCollection([])
    calls = []
    original = worker.crawl_all
    worker.crawl_all = lambda kws: calls.append(kws)
    try:
        worker.run_once(collection=collection)
        assert calls == [], "큐가 비었는데 크롤링이 호출됨"
    finally:
        worker.crawl_all = original
    print("[OK] 빈 큐는 조용히 반환")


def test_정상_처리시_done으로_바뀐다():
    collection = _FakeCollection([
        {"_id": "야르", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    calls = {}
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    worker.crawl_all = lambda kws: calls.setdefault("crawl", kws)
    worker.preprocess_documents = lambda kw: calls.setdefault("preprocess", kw)
    worker.embed_documents = lambda kw: calls.setdefault("embed", kw)
    try:
        worker.run_once(collection=collection)
        assert calls == {"crawl": ["야르"], "preprocess": "야르", "embed": "야르"}, calls
        assert collection._docs["야르"]["status"] == "done", collection._docs["야르"]
    finally:
        worker.crawl_all = original_crawl
        worker.preprocess_documents = original_pre
        worker.embed_documents = original_embed
    print("[OK] 정상 처리 시 done + 3단계 순서대로 호출")


def test_예외_발생시_failed와_에러메시지가_기록된다():
    collection = _FakeCollection([
        {"_id": "쌰갈", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    original = worker.crawl_all
    worker.crawl_all = lambda kws: (_ for _ in ()).throw(RuntimeError("크롤 실패 테스트"))
    try:
        worker.run_once(collection=collection)
        doc = collection._docs["쌰갈"]
        assert doc["status"] == "failed", doc
        assert "크롤 실패 테스트" in doc["error"], doc
    finally:
        worker.crawl_all = original
    print("[OK] 예외 발생 시 failed + error 메시지 기록")


def test_가장_오래된_큐_항목부터_처리한다():
    collection = _FakeCollection([
        {"_id": "나중요청", "status": "queued", "requested_at": datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
        {"_id": "먼저요청", "status": "queued", "requested_at": datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    calls = []
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    worker.crawl_all = lambda kws: calls.append(kws[0])
    worker.preprocess_documents = lambda kw: None
    worker.embed_documents = lambda kw: None
    try:
        worker.run_once(collection=collection)
        assert calls == ["먼저요청"], calls
    finally:
        worker.crawl_all = original_crawl
        worker.preprocess_documents = original_pre
        worker.embed_documents = original_embed
    print("[OK] requested_at이 가장 오래된 것부터 처리")


if __name__ == "__main__":
    test_큐가_비어있으면_아무것도_안한다()
    test_정상_처리시_done으로_바뀐다()
    test_예외_발생시_failed와_에러메시지가_기록된다()
    test_가장_오래된_큐_항목부터_처리한다()
    print("\nALL PASS ✅")

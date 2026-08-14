"""
crawl_request_worker.run_once() 단위 테스트.
실제 Mongo/크롤러/임베딩 없이 fake collection + monkeypatch로 오케스트레이션만 검증.

실행: uv run python tests/test_crawl_request_worker.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.crawl_request_worker as worker


def _apply_set(doc, changes):
    """$set을 적용한다. 'progress.dcinside'처럼 점 표기 경로도 Mongo와 같게 중첩 반영한다
    (진행 콜백이 필드 단위 $set을 쓰기 때문 — 통째로 덮어쓰면 스레드끼리 갱신을 잃는다)."""
    for key, value in changes.items():
        if "." not in key:
            doc[key] = value
            continue
        head, _, tail = key.partition(".")
        doc.setdefault(head, {})[tail] = value


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
        _apply_set(doc, update["$set"])
        return doc

    def update_one(self, filter_, update):
        doc = self._docs[filter_["_id"]]
        _apply_set(doc, update["$set"])

    def update_many(self, filter_, update):
        """단순한 $lt 비교 + 동등 비교만 지원 (run_once의 _requeue_stale_running이 쓰는 형태)."""
        def matches(doc):
            for key, cond in filter_.items():
                value = doc.get(key)
                if isinstance(cond, dict):
                    if "$lt" in cond and not (value is not None and value < cond["$lt"]):
                        return False
                elif value != cond:
                    return False
            return True

        for doc in self._docs.values():
            if matches(doc):
                doc.update(update["$set"])


class _FakeLlmRequestsCollection:
    def __init__(self, docs=None):
        self.deleted_filters = []
        self._docs = {d["_id"]: d for d in (docs or [])}

    def delete_many(self, filter_):
        self.deleted_filters.append(filter_)
        to_delete = [
            _id for _id, doc in self._docs.items()
            if all(doc.get(k) == v for k, v in filter_.items())
        ]
        for _id in to_delete:
            del self._docs[_id]


def test_큐가_비어있으면_아무것도_안한다():
    collection = _FakeCollection([])
    calls = []
    original = worker.crawl_all
    worker.crawl_all = lambda kws, on_source_done=None: calls.append(kws)
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
    fake_llm = _FakeLlmRequestsCollection()
    calls = {}
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    original_lock, original_release = worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock
    def fake_embed(kw):
        calls["embed"] = kw
        return {"documents": 1, "chunks": 3}

    worker.crawl_all = lambda kws, on_source_done=None: calls.setdefault("crawl", kws)
    worker.preprocess_documents = lambda kw: calls.setdefault("preprocess", kw)
    worker.embed_documents = fake_embed
    worker.acquire_heavy_job_lock_blocking = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        assert calls == {"crawl": ["야르"], "preprocess": "야르", "embed": "야르"}, calls
        assert collection._docs["야르"]["status"] == "done", collection._docs["야르"]
        assert fake_llm.deleted_filters == [{"keyword": "야르", "status": "done"}], fake_llm.deleted_filters
    finally:
        worker.crawl_all, worker.preprocess_documents, worker.embed_documents = original_crawl, original_pre, original_embed
        worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock = original_lock, original_release
    print("[OK] 정상 처리 시 done + 3단계 순서대로 호출 + llm_requests 캐시 무효화")


def test_예외_발생시_failed와_에러메시지가_기록된다():
    collection = _FakeCollection([
        {"_id": "쌰갈", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    original = worker.crawl_all
    worker.crawl_all = lambda kws, on_source_done=None: (_ for _ in ()).throw(RuntimeError("크롤 실패 테스트"))
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
    fake_llm = _FakeLlmRequestsCollection()
    calls = []
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    original_lock, original_release = worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock
    worker.crawl_all = lambda kws, on_source_done=None: calls.append(kws[0])
    worker.preprocess_documents = lambda kw: None
    worker.embed_documents = lambda kw: {"documents": 1, "chunks": 1}
    worker.acquire_heavy_job_lock_blocking = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        assert calls == ["먼저요청"], calls
    finally:
        worker.crawl_all = original_crawl
        worker.preprocess_documents = original_pre
        worker.embed_documents = original_embed
        worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock = original_lock, original_release
    print("[OK] requested_at이 가장 오래된 것부터 처리")


def test_임베딩_결과가_0건이면_failed로_기록된다():
    collection = _FakeCollection([
        {"_id": "존재안함", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    fake_llm = _FakeLlmRequestsCollection()
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    original_lock, original_release = worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock
    worker.crawl_all = lambda kws, on_source_done=None: None
    worker.preprocess_documents = lambda kw: None
    worker.embed_documents = lambda kw: {"documents": 0, "chunks": 0, "failed": 0, "near_dup_skipped": 0}
    worker.acquire_heavy_job_lock_blocking = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        doc = collection._docs["존재안함"]
        assert doc["status"] == "failed", doc
        assert doc.get("error"), "0건 임베딩인데 error 메시지가 비어있음"
        assert fake_llm.deleted_filters == [], fake_llm.deleted_filters
    finally:
        worker.crawl_all = original_crawl
        worker.preprocess_documents = original_pre
        worker.embed_documents = original_embed
        worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock = original_lock, original_release
    print("[OK] 임베딩 결과 0건이면 done이 아니라 failed + 에러 메시지 기록")


def test_오래된_running_요청은_requeue되어_같은_실행에서_처리된다():
    """max_instances=1은 인메모리 보장이라, 프로세스가 죽으면 running 문서만 남는다.
    _requeue_stale_running이 run_once 맨 앞에서 그걸 회수해 queued로 돌리므로,
    다른 queued 요청이 없다면 바로 이번 run_once 안에서 처리까지 이어진다."""
    collection = _FakeCollection([
        {"_id": "고아된요청", "status": "running",
         "requested_at": datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc),
         "started_at": datetime.now(timezone.utc) - timedelta(hours=3),
         "completed_at": None, "error": None},
    ])
    fake_llm = _FakeLlmRequestsCollection()
    calls = []
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    original_lock, original_release = worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock
    worker.crawl_all = lambda kws, on_source_done=None: calls.append(kws[0])
    worker.preprocess_documents = lambda kw: None
    worker.embed_documents = lambda kw: {"documents": 1, "chunks": 1}
    worker.acquire_heavy_job_lock_blocking = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        doc = collection._docs["고아된요청"]
        assert calls == ["고아된요청"], f"고아 상태(running, 3시간 전)가 requeue되어 처리됐어야 함: {calls}"
        assert doc["status"] == "done", doc
    finally:
        worker.crawl_all = original_crawl
        worker.preprocess_documents = original_pre
        worker.embed_documents = original_embed
        worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock = original_lock, original_release
    print("[OK] 오래된 running은 requeue되어 같은 run_once 안에서 처리됨")


def test_최근_running_요청은_requeue되지_않는다():
    """진짜 진행 중인 작업(started_at이 최근)은 건드리면 안 된다 — 이중 처리 방지."""
    collection = _FakeCollection([
        {"_id": "진행중인요청", "status": "running",
         "requested_at": datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc),
         "started_at": datetime.now(timezone.utc) - timedelta(minutes=5),
         "completed_at": None, "error": None},
    ])
    calls = []
    original_crawl = worker.crawl_all
    worker.crawl_all = lambda kws, on_source_done=None: calls.append(kws[0])
    try:
        worker.run_once(collection=collection)
        doc = collection._docs["진행중인요청"]
        assert calls == [], f"진행 중인 작업인데 크롤링이 다시 호출됨: {calls}"
        assert doc["status"] == "running", doc
    finally:
        worker.crawl_all = original_crawl
    print("[OK] 최근 running은 requeue되지 않고 그대로 유지됨")


def test_임베딩_락을_못잡으면_failed로_기록되고_임베딩은_호출되지_않는다():
    collection = _FakeCollection([
        {"_id": "야르", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    fake_llm = _FakeLlmRequestsCollection()
    calls = {}
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    original_lock = worker.acquire_heavy_job_lock_blocking
    worker.crawl_all = lambda kws, on_source_done=None: calls.setdefault("crawl", kws)
    worker.preprocess_documents = lambda kw: calls.setdefault("preprocess", kw)
    worker.embed_documents = lambda kw: calls.setdefault("embed_called", True)
    worker.acquire_heavy_job_lock_blocking = lambda owner: False
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        doc = collection._docs["야르"]
        assert doc["status"] == "failed", doc
        assert "락" in doc["error"], doc
        assert "embed_called" not in calls, "락을 못 잡았으면 embed_documents가 호출되면 안 됨"
        assert fake_llm.deleted_filters == [], "실패했으니 캐시 무효화도 안 해야 함"
    finally:
        worker.crawl_all, worker.preprocess_documents, worker.embed_documents = original_crawl, original_pre, original_embed
        worker.acquire_heavy_job_lock_blocking = original_lock
    print("[OK] 임베딩 락 타임아웃 -> failed, embed_documents 미호출")


def test_예외_발생시_락_함수가_호출되지_않은_경우에도_안전하다():
    """crawl_all 단계에서 이미 예외가 나면 락 근처에도 안 가야 한다(불필요한 락 시도 금지)."""
    collection = _FakeCollection([
        {"_id": "쌰갈", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    fake_llm = _FakeLlmRequestsCollection()
    original_crawl = worker.crawl_all
    original_lock = worker.acquire_heavy_job_lock_blocking
    worker.crawl_all = lambda kws, on_source_done=None: (_ for _ in ()).throw(RuntimeError("크롤 실패 테스트"))
    worker.acquire_heavy_job_lock_blocking = lambda owner: (_ for _ in ()).throw(AssertionError("호출되면 안 됨"))
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        doc = collection._docs["쌰갈"]
        assert doc["status"] == "failed", doc
        assert "크롤 실패 테스트" in doc["error"], doc
    finally:
        worker.crawl_all = original_crawl
        worker.acquire_heavy_job_lock_blocking = original_lock
    print("[OK] crawl_all 단계 예외는 락 시도 전에 걸러짐")


def test_delete_many이_done_상태만_지우고_진행중인_요청은_보존한다():
    """crawl_request_worker가 완료 후 llm_requests 캐시를 무효화할 때, 같은 keyword로
    이미 캐시된(done) 결과만 지워야 한다. embed_documents가 문서 단위로
    is_embedded를 켜는 도중 list_analyzable_keywords()가 그 keyword를 조기에
    노출해 다른 클라이언트가 analyze/rag 요청을 새로 만들면, 그 요청(queued/running)
    문서까지 delete_many가 지워버리면 폴링 중이던 클라이언트가 404를 받는
    버그가 될 수 있다 — status: done 필터로 그걸 막는다."""
    collection = _FakeCollection([
        {"_id": "야르", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    fake_llm = _FakeLlmRequestsCollection([
        {"_id": "docA", "keyword": "야르", "status": "done"},
        {"_id": "docB", "keyword": "야르", "status": "running"},
    ])
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    original_lock, original_release = worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock
    worker.crawl_all = lambda kws, on_source_done=None: None
    worker.preprocess_documents = lambda kw: None
    worker.embed_documents = lambda kw: {"documents": 1, "chunks": 1}
    worker.acquire_heavy_job_lock_blocking = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        assert "docA" not in fake_llm._docs, "done 상태의 캐시 문서는 지워졌어야 함"
        assert "docB" in fake_llm._docs, "running 상태의 진행 중인 요청은 보존됐어야 함"
    finally:
        worker.crawl_all, worker.preprocess_documents, worker.embed_documents = original_crawl, original_pre, original_embed
        worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock = original_lock, original_release
    print("[OK] delete_many는 done 상태만 지우고 진행 중인 요청은 보존함")


if __name__ == "__main__":
    test_큐가_비어있으면_아무것도_안한다()
    test_정상_처리시_done으로_바뀐다()
    test_예외_발생시_failed와_에러메시지가_기록된다()
    test_가장_오래된_큐_항목부터_처리한다()
    test_임베딩_결과가_0건이면_failed로_기록된다()
    test_오래된_running_요청은_requeue되어_같은_실행에서_처리된다()
    test_최근_running_요청은_requeue되지_않는다()
    test_임베딩_락을_못잡으면_failed로_기록되고_임베딩은_호출되지_않는다()
    test_예외_발생시_락_함수가_호출되지_않은_경우에도_안전하다()
    test_delete_many이_done_상태만_지우고_진행중인_요청은_보존한다()
    print("\nALL PASS ✅")

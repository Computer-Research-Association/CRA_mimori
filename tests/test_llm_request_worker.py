"""
llm_request_worker.run_once() / run_loop() 단위 테스트.
실제 Mongo/Qdrant/임베딩/LLM 없이 fake collection + monkeypatch로 오케스트레이션만 검증.

실행: uv run python tests/test_llm_request_worker.py
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.llm_request_worker as worker


class _FakeCollection:
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

    def update_many(self, filter_, update):
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


def _analyze_doc(status="queued", sources=None):
    return {
        "_id": "야르", "keyword": "야르", "sources": sources,
        "status": status, "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    }


def test_큐가_비어있으면_아무것도_안한다():
    collection = _FakeCollection([])
    original_lock = worker.acquire_heavy_job_lock
    worker.acquire_heavy_job_lock = lambda owner: (_ for _ in ()).throw(AssertionError("호출되면 안 됨"))
    try:
        did_work = worker.run_once(collection=collection)
        assert did_work is False, "빈 큐는 False를 돌려줘야 run_loop가 idle로 판단함"
    finally:
        worker.acquire_heavy_job_lock = original_lock
    print("[OK] 빈 큐는 조용히 반환(락도 안 건드림) + False 반환")


def test_analyze_작업이_정상_처리되면_done으로_바뀐다():
    collection = _FakeCollection([_analyze_doc()])
    calls = {}
    original = (
        worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
        worker.default_facet_config, worker.encode_facets, worker.facet_search,
        worker.get_cached_trend, worker.format_trend_context,
        worker.build_facet_prompt, worker.analyze, worker.clean_source_url,
    )
    worker.acquire_heavy_job_lock = lambda owner: calls.setdefault("lock_acquired", owner) or True
    worker.release_heavy_job_lock = lambda owner: calls.setdefault("lock_released", owner)
    worker.default_facet_config = lambda keyword: {"의미": {"question": keyword}}
    worker.encode_facets = lambda facet_config: {"의미": {"dense": [], "sparse": {}}}
    fake_point = SimpleNamespace(payload={"text": "청크", "title": "제목", "url": "https://example.com"})

    def fake_facet_search(keyword, facet_config, facet_vectors=None, sources=None, is_relevant=None):
        calls["facet_search_sources"] = sources
        return [fake_point], {}

    worker.facet_search = fake_facet_search
    worker.get_cached_trend = lambda keyword: {"status": "유행 중"}
    worker.format_trend_context = lambda keyword, result=None: "트렌드요약"
    worker.build_facet_prompt = lambda keyword, points, trend_info=None: "프롬프트"
    worker.analyze = lambda prompt: calls.setdefault("analyze_prompt", prompt) and "분석 결과"
    worker.clean_source_url = lambda url: url
    try:
        did_work = worker.run_once(collection=collection)
        assert did_work is True, "요청을 처리했으면 True를 돌려줘야 함"
        doc = collection._docs["야르"]
        assert doc["status"] == "done", doc
        assert doc["result"]["result"] == "분석 결과", doc
        assert doc["result"]["trend"] == {"status": "유행 중"}, doc
        assert doc["result"]["sources"] == [{"title": "제목", "url": "https://example.com"}], doc
        assert calls["lock_acquired"] == "llm_request_worker", calls
        assert calls["lock_released"] == "llm_request_worker", calls
        assert calls["facet_search_sources"] is None, "sources 생략 시 None이 그대로 전달돼야 함"
    finally:
        (worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
         worker.default_facet_config, worker.encode_facets, worker.facet_search,
         worker.get_cached_trend, worker.format_trend_context,
         worker.build_facet_prompt, worker.analyze, worker.clean_source_url) = original
    print("[OK] analyze 작업 정상 처리 -> done + 락 획득/해제")


def test_요청에_담긴_sources가_facet_search로_전달된다():
    collection = _FakeCollection([_analyze_doc(sources=["tavily", "youtube"])])
    calls = {}
    original = (
        worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
        worker.default_facet_config, worker.encode_facets, worker.facet_search,
        worker.get_cached_trend, worker.build_facet_prompt, worker.analyze, worker.clean_source_url,
    )
    worker.acquire_heavy_job_lock = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    worker.default_facet_config = lambda keyword: {"의미": {"question": keyword}}
    worker.encode_facets = lambda facet_config: {"의미": {"dense": [], "sparse": {}}}
    fake_point = SimpleNamespace(payload={"text": "청크", "title": "제목", "url": "https://example.com"})

    def fake_facet_search(keyword, facet_config, facet_vectors=None, sources=None, is_relevant=None):
        calls["sources"] = sources
        return [fake_point], {}

    worker.facet_search = fake_facet_search
    worker.get_cached_trend = lambda keyword: None
    worker.build_facet_prompt = lambda keyword, points, trend_info=None: "프롬프트"
    worker.analyze = lambda prompt: "결과"
    worker.clean_source_url = lambda url: url
    try:
        worker.run_once(collection=collection)
        assert calls["sources"] == ["tavily", "youtube"], calls
    finally:
        (worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
         worker.default_facet_config, worker.encode_facets, worker.facet_search,
         worker.get_cached_trend, worker.build_facet_prompt, worker.analyze, worker.clean_source_url) = original
    print("[OK] 요청에 담긴 sources가 facet_search로 그대로 전달됨")


def test_결과가_없으면_failed로_기록된다():
    collection = _FakeCollection([_analyze_doc()])
    original = (
        worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
        worker.default_facet_config, worker.encode_facets, worker.facet_search,
    )
    worker.acquire_heavy_job_lock = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    worker.default_facet_config = lambda keyword: {}
    worker.encode_facets = lambda facet_config: {}
    worker.facet_search = lambda keyword, facet_config, facet_vectors=None, sources=None, is_relevant=None: ([], {})
    try:
        worker.run_once(collection=collection)
        doc = collection._docs["야르"]
        assert doc["status"] == "failed", doc
        assert "데이터를 찾을 수 없습니다" in doc["error"], doc
    finally:
        (worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
         worker.default_facet_config, worker.encode_facets, worker.facet_search) = original
    print("[OK] 검색 결과 없음 -> failed + 에러 메시지")


def test_락을_못잡으면_다시_queued로_돌리고_반환한다():
    collection = _FakeCollection([_analyze_doc()])
    original_lock = worker.acquire_heavy_job_lock
    worker.acquire_heavy_job_lock = lambda owner: False
    try:
        did_work = worker.run_once(collection=collection)
        assert did_work is True, "락 경합은 빈 큐가 아니므로 True(=유휴 아님)를 돌려줘야 함"
        doc = collection._docs["야르"]
        assert doc["status"] == "queued", doc
        assert doc["started_at"] is None, doc
    finally:
        worker.acquire_heavy_job_lock = original_lock
    print("[OK] 락 획득 실패 -> queued로 되돌리고 이번 틱은 skip (True 반환)")


def test_예외_발생시_failed와_에러메시지가_기록되고_락이_해제된다():
    collection = _FakeCollection([_analyze_doc()])
    calls = {}
    original = (
        worker.acquire_heavy_job_lock, worker.release_heavy_job_lock, worker.default_facet_config,
    )
    worker.acquire_heavy_job_lock = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: calls.setdefault("released", True)
    worker.default_facet_config = lambda keyword: (_ for _ in ()).throw(RuntimeError("설정 생성 실패"))
    try:
        worker.run_once(collection=collection)
        doc = collection._docs["야르"]
        assert doc["status"] == "failed", doc
        assert "설정 생성 실패" in doc["error"], doc
        assert calls.get("released") is True, "예외가 나도 락은 해제돼야 함"
    finally:
        (worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
         worker.default_facet_config) = original
    print("[OK] 예외 발생 시 failed + 락 해제 보장")


def test_오래된_running_요청은_requeue된다():
    doc = _analyze_doc(status="running")
    doc["started_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    collection = _FakeCollection([doc])
    original = (
        worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
        worker.default_facet_config, worker.encode_facets, worker.facet_search,
        worker.get_cached_trend, worker.build_facet_prompt, worker.analyze, worker.clean_source_url,
    )
    worker.acquire_heavy_job_lock = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    worker.default_facet_config = lambda keyword: {"의미": {"question": keyword}}
    worker.encode_facets = lambda facet_config: {"의미": {"dense": [], "sparse": {}}}
    fake_point = SimpleNamespace(payload={"text": "청크", "title": "제목", "url": "https://example.com"})
    worker.facet_search = lambda keyword, facet_config, facet_vectors=None, sources=None, is_relevant=None: ([fake_point], {})
    worker.get_cached_trend = lambda keyword: None
    worker.build_facet_prompt = lambda keyword, points, trend_info=None: "프롬프트"
    worker.analyze = lambda prompt: "결과"
    worker.clean_source_url = lambda url: url
    try:
        worker.run_once(collection=collection)
        assert collection._docs["야르"]["status"] == "done", collection._docs["야르"]
    finally:
        (worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
         worker.default_facet_config, worker.encode_facets, worker.facet_search,
         worker.get_cached_trend, worker.build_facet_prompt, worker.analyze, worker.clean_source_url) = original
    print("[OK] 20분 넘게 running이던 요청은 requeue되어 같은 실행에서 처리됨")


def test_run_loop는_큐가_계속_비면_idle_timeout_후_스스로_종료한다():
    """BGE-M3를 매 요청마다 새로 로드하지 않으려고 상주시키는 게 run_loop의
    목적인데, 유휴 상태로 영원히 안 죽으면 메모리를 무한정 점유한다.
    idle_timeout을 넘겨도 계속 빈 큐면 반드시 빠져나와야 한다."""
    calls = {"n": 0}
    original = (worker.run_once, worker.LLM_WORKER_POLL_INTERVAL_SECONDS, worker.get_collection)
    worker.run_once = lambda collection=None: calls.__setitem__("n", calls["n"] + 1) or False
    worker.LLM_WORKER_POLL_INTERVAL_SECONDS = 0.01
    worker.get_collection = lambda name: None
    try:
        start = time.monotonic()
        worker.run_loop(idle_timeout=0.05)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"idle_timeout(0.05s) 근처에서 끝나야 하는데 {elapsed:.2f}s 걸림"
        assert calls["n"] >= 2, f"적어도 몇 번은 폴링해야 함: {calls}"
    finally:
        worker.run_once, worker.LLM_WORKER_POLL_INTERVAL_SECONDS, worker.get_collection = original
    print(f"[OK] run_loop: 빈 큐가 idle_timeout 넘으면 스스로 종료 (elapsed={elapsed:.3f}s, polls={calls['n']})")


def test_run_loop는_처리할_일이_있으면_idle_타이머가_리셋된다():
    """일하다가 큐가 비기 시작하면, 그 시점부터 다시 idle_timeout을 꽉 채워야
    종료해야 한다 — 마지막 작업 직후 곧바로 끊기면 안 됨."""
    results = iter([True, True, False, False, False, False, False, False])
    calls = {"n": 0}

    def fake_run_once(collection=None):
        calls["n"] += 1
        return next(results, False)

    original = (worker.run_once, worker.LLM_WORKER_POLL_INTERVAL_SECONDS, worker.get_collection)
    worker.run_once = fake_run_once
    worker.LLM_WORKER_POLL_INTERVAL_SECONDS = 0.01
    worker.get_collection = lambda name: None
    try:
        worker.run_loop(idle_timeout=0.03)
        # True가 2번 나온 뒤부터 idle 카운트 시작 -> poll(0.01s) 몇 번은 더 돌아야 0.03s를 채움
        assert calls["n"] >= 5, calls
    finally:
        worker.run_once, worker.LLM_WORKER_POLL_INTERVAL_SECONDS, worker.get_collection = original
    print(f"[OK] run_loop: 작업이 있으면 idle 타이머가 리셋됨 (polls={calls['n']})")


if __name__ == "__main__":
    test_큐가_비어있으면_아무것도_안한다()
    test_analyze_작업이_정상_처리되면_done으로_바뀐다()
    test_요청에_담긴_sources가_facet_search로_전달된다()
    test_결과가_없으면_failed로_기록된다()
    test_락을_못잡으면_다시_queued로_돌리고_반환한다()
    test_예외_발생시_failed와_에러메시지가_기록되고_락이_해제된다()
    test_오래된_running_요청은_requeue된다()
    test_run_loop는_큐가_계속_비면_idle_timeout_후_스스로_종료한다()
    test_run_loop는_처리할_일이_있으면_idle_타이머가_리셋된다()
    print("\nALL PASS ✅")

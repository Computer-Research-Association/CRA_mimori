# analyze/rag 성능 개선 + 비동기화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/api/analyze`, `/api/rag`가 동기 요청 안에서 임베딩+검색+LLM 호출을 전부 처리해 최대 10분까지 걸리는 문제를, 캐싱 + 비동기 큐(scheduler 처리) + facet 병렬 검색 + 토큰 상한 축소로 개선한다.

**Architecture:** API는 `llm_requests` Mongo 컬렉션에 큐잉/조회만 하는 가벼운 프로세스로 남고, 실제 임베딩(BGE-M3)+Qdrant 검색+LLM 호출은 scheduler 컨테이너가 5초마다 큐를 확인해 서브프로세스로 처리한다(기존 `crawl_requests`와 동일 패턴). 완료된 `llm_requests` 문서 자체가 캐시 역할을 겸한다. 크롤 워커와 analyze/rag 워커가 동시에 BGE-M3를 메모리에 올리지 않도록 Mongo 기반 `heavy_job_lock`으로 상호 배제한다.

**Tech Stack:** Python(Flask, pymongo, qdrant-client, APScheduler), React(vitest, testing-library).

## Global Constraints

- 스펙 문서: [docs/superpowers/specs/2026-08-11-analyze-rag-async-perf-design.md](../specs/2026-08-11-analyze-rag-async-perf-design.md) — 모든 태스크는 이 문서의 아키텍처/데이터 모델을 그대로 따른다.
- **커밋은 사용자가 직접 수행한다.** 각 태스크는 테스트 통과 확인까지만 진행하고 `git commit`은 실행하지 않는다(사용자가 명시적으로 요청함).
- `/api/analyze`, `/api/rag`(동기 엔드포인트)는 완전히 제거하고 `/api/analyze-request`·`/api/rag-request`로 대체한다. 하위호환 불필요(호출부는 프론트와 테스트뿐).
- 백엔드 테스트는 pytest가 아니라 이 repo의 관례대로 `assert` + `if __name__ == "__main__":` 스크립트 형태로 작성하고 `uv run python tests/파일명.py`로 실행한다. Mongo/Qdrant/LLM은 항상 monkeypatch/fake로 대체하고 실제 네트워크 호출을 하지 않는다.
- 프론트 테스트는 vitest + testing-library, `frontend/` 안에서 `npm test`로 실행한다.

---

### Task 1: LLM 출력 토큰 상한 축소 (16384 → 4096)

**Files:**
- Modify: `analysis/pipeline.py:173-185` (`analyze()` 함수)
- Test: `tests/test_analyze_token_limit.py` (신규)

**Interfaces:**
- Consumes: 없음(독립 태스크)
- Produces: `analyze(prompt, model=ANALYSIS_MODEL)`의 동작은 그대로, `max_completion_tokens`만 4096으로 변경. 이후 태스크들이 이 값을 재검증할 필요 없음.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_analyze_token_limit.py`:
```python
"""
analyze()가 ChatNVIDIA를 생성할 때 max_completion_tokens=4096으로 호출하는지 확인.
실제 NVIDIA API 호출은 하지 않는다 — ChatNVIDIA 클래스 자체를 fake로 바꿔치기한다.

실행: uv run python tests/test_analyze_token_limit.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.pipeline as pipeline


class _FakeResponse:
    content = "가짜 응답"


class _FakeChatNVIDIA:
    captured_kwargs = None

    def __init__(self, **kwargs):
        _FakeChatNVIDIA.captured_kwargs = kwargs

    def invoke(self, messages):
        return _FakeResponse()


def test_max_completion_tokens는_4096이다():
    original = pipeline.ChatNVIDIA
    pipeline.ChatNVIDIA = _FakeChatNVIDIA
    try:
        result = pipeline.analyze("테스트 프롬프트")
        assert result == "가짜 응답", result
        assert _FakeChatNVIDIA.captured_kwargs["max_completion_tokens"] == 4096, _FakeChatNVIDIA.captured_kwargs
    finally:
        pipeline.ChatNVIDIA = original
    print("[OK] max_completion_tokens=4096")


if __name__ == "__main__":
    test_max_completion_tokens는_4096이다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run python tests/test_analyze_token_limit.py`
Expected: `AssertionError` — `captured_kwargs["max_completion_tokens"]`가 16384라서 4096과 불일치.

- [ ] **Step 3: 구현 — 토큰 상한 변경**

`analysis/pipeline.py:180`에서:
```python
        max_completion_tokens=16384,
```
를
```python
        max_completion_tokens=4096,
```
로 변경.

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `uv run python tests/test_analyze_token_limit.py`
Expected: `ALL PASS ✅`

---

### Task 2: `facet_search` 4개 facet 병렬 검색

**Files:**
- Modify: `analysis/rag_pipeline.py` (상단 import에 `concurrent.futures` 추가, `facet_search` 함수의 456-471행 순차 for 루프를 병렬로 교체)
- Test: `tests/test_facet_search_parallel.py` (신규)

**Interfaces:**
- Consumes: 없음(독립 태스크)
- Produces: `facet_search(keyword, facet_config, facet_vectors=None, sources=None, is_relevant=None, ...) -> tuple[list, dict]` — 외부 시그니처/반환 형식 불변(내부 실행만 병렬화). `diagnostics["facet_points"]`는 기존과 동일하게 `{facet명: [ScoredPoint,...]}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_facet_search_parallel.py`:
```python
"""
facet_search가 4개 facet을 병렬로 검색하는지(순차 실행보다 훨씬 빠른지) +
facet별 결과가 올바르게 모이는지 확인. 실제 Qdrant 호출 없음
(search_relevant_chunks를 sleep 포함 fake로 대체).

실행: uv run python tests/test_facet_search_parallel.py
"""
import os
import sys
import threading
import time
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.rag_pipeline as rag_pipeline
from analysis.rag_pipeline import facet_search

_WORDS = ["사과바나나사과", "컴퓨터키보드모니터", "하늘구름비바람", "축구농구야구배구"]
_word_lock = threading.Lock()
_word_index = {"n": 0}


def _fake_search_relevant_chunks(keyword, dense_vec, sparse, **kwargs):
    time.sleep(0.15)  # 실제 Qdrant 왕복 시간을 흉내냄
    with _word_lock:
        word = _WORDS[_word_index["n"] % len(_WORDS)]
        _word_index["n"] += 1
    return [SimpleNamespace(
        id=uuid.uuid4().hex,
        score=1.0,
        payload={"text": f"{keyword} {word}", "title": "제목", "url": "https://example.com", "source": "tavily"},
    )]


def _facet_config():
    common = {"top_k": 5, "min_length": 30, "over_fetch_factor": 3, "min_dense_score": 0.3, "max_per_source": 2}
    return {
        "의미": {"question": "야르, 무슨 의미?", **common},
        "유행_이유": {"question": "야르, 왜 유행했나요?", **common},
        "사용법": {"question": "야르, 어떻게 사용하나요?", **common},
        "사용자층": {"question": "야르, 주로 누가 사용하나요?", **common},
    }


def test_4개_facet을_병렬로_검색해_각각_결과를_모은다():
    _word_index["n"] = 0
    original = rag_pipeline.search_relevant_chunks
    rag_pipeline.search_relevant_chunks = _fake_search_relevant_chunks
    try:
        facet_config = _facet_config()
        facet_vectors = {name: {"dense": [0.1], "sparse": {}} for name in facet_config}

        start = time.monotonic()
        merged_points, diagnostics = facet_search("야르", facet_config, facet_vectors=facet_vectors, is_relevant=True)
        elapsed = time.monotonic() - start

        assert elapsed < 0.35, f"4개를 병렬로 돌렸다면 0.35초 안에 끝나야 함(순차면 약 0.6초): {elapsed:.2f}s"
        assert set(diagnostics["facet_points"].keys()) == set(facet_config.keys()), diagnostics["facet_points"].keys()
        for name, points in diagnostics["facet_points"].items():
            assert len(points) == 1, (name, points)
        assert len(merged_points) >= 1, merged_points
    finally:
        rag_pipeline.search_relevant_chunks = original
    print(f"[OK] facet 4개 병렬 검색 완료 (elapsed={elapsed:.3f}s), facet별 결과 정상 취합")


if __name__ == "__main__":
    test_4개_facet을_병렬로_검색해_각각_결과를_모은다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run python tests/test_facet_search_parallel.py`
Expected: `AssertionError` — 현재는 순차 실행이라 `elapsed`가 약 0.6초로 0.35초 기준을 넘음.

- [ ] **Step 3: 구현 — 병렬 검색으로 교체**

`analysis/rag_pipeline.py` 상단 import 블록(7번째 줄 `import difflib` 다음)에 추가:
```python
import concurrent.futures
```

`analysis/rag_pipeline.py`의 `facet_search` 함수 내부(456번째 줄 부근, `facet_order = list(facet_config.keys())`부터 `search_relevant_chunks(...)` 호출까지의 for 루프)를 찾아서:

기존:
```python
    facet_order = list(facet_config.keys())
    facet_points: dict[str, list[models.ScoredPoint]] = {}
    for name in facet_order:
        cfg = facet_config[name]
        vecs = facet_vectors[name]
        facet_points[name] = search_relevant_chunks(
            keyword, vecs["dense"], vecs["sparse"],
            top_k=cfg["top_k"], sources=sources, is_relevant=is_relevant,
            min_length=cfg["min_length"], over_fetch_factor=cfg["over_fetch_factor"],
            min_dense_score=cfg["min_dense_score"], max_per_source=cfg["max_per_source"],
        )
```

를 다음으로 교체:
```python
    facet_order = list(facet_config.keys())

    def _search_one_facet(name: str) -> tuple[str, list[models.ScoredPoint]]:
        cfg = facet_config[name]
        vecs = facet_vectors[name]
        points = search_relevant_chunks(
            keyword, vecs["dense"], vecs["sparse"],
            top_k=cfg["top_k"], sources=sources, is_relevant=is_relevant,
            min_length=cfg["min_length"], over_fetch_factor=cfg["over_fetch_factor"],
            min_dense_score=cfg["min_dense_score"], max_per_source=cfg["max_per_source"],
        )
        return name, points

    facet_points: dict[str, list[models.ScoredPoint]] = {}
    # Qdrant 쿼리는 I/O bound라 스레드가 대기 중 GIL을 놓는다 — 4개를 동시에 보내면
    # 순차 실행(4배 시간) 대신 가장 느린 facet 하나만큼의 시간으로 끝난다.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(facet_order)) as executor:
        for name, points in executor.map(_search_one_facet, facet_order):
            facet_points[name] = points
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `uv run python tests/test_facet_search_parallel.py`
Expected: `ALL PASS ✅`

---

### Task 3: `heavy_job_lock` — 크롤/analyze 워커 간 BGE-M3 동시 로드 방지 락

**Files:**
- Create: `scripts/heavy_job_lock.py`
- Modify: `config/config_cilent.py:20` 다음 줄에 `LOCKS_COLLECTION` 추가
- Test: `tests/test_heavy_job_lock.py` (신규)

**Interfaces:**
- Consumes: `DB.mongo_client.get_collection`, `config.config_cilent.LOCKS_COLLECTION`
- Produces (Task 5, 6이 사용):
  - `acquire_heavy_job_lock(owner: str, collection=None) -> bool`
  - `release_heavy_job_lock(owner: str, collection=None) -> None`
  - `acquire_heavy_job_lock_blocking(owner: str, timeout: timedelta = timedelta(minutes=10), poll_interval: float = 5.0, collection=None) -> bool`

- [ ] **Step 1: config에 `LOCKS_COLLECTION` 추가**

`config/config_cilent.py:20` (`HIDDEN_KEYWORDS_COLLECTION = "hidden_keywords"   # ...` 다음 줄)에 추가:
```python
LOCKS_COLLECTION = "locks"                        # heavy_job_lock 등 프로세스 간 락
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_heavy_job_lock.py`:
```python
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
    print("\nALL PASS ✅")
```

- [ ] **Step 3: 테스트 실행해 실패 확인**

Run: `uv run python tests/test_heavy_job_lock.py`
Expected: `ModuleNotFoundError: No module named 'scripts.heavy_job_lock'`

- [ ] **Step 4: 구현 — `scripts/heavy_job_lock.py` 작성**

```python
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
```

- [ ] **Step 5: 테스트 실행해 통과 확인**

Run: `uv run python tests/test_heavy_job_lock.py`
Expected: `ALL PASS ✅`

---

### Task 4: `llm_requests` 큐/캐시 API — `/api/analyze-request`, `/api/rag-request`

**Files:**
- Modify: `config/config_cilent.py:20` 다음 줄(Task 3에서 추가한 `LOCKS_COLLECTION` 다음 줄)에 `LLM_REQUESTS_COLLECTION` 추가
- Modify: `api/routes.py` — `/analyze`, `/rag` 제거, `/analyze-request`, `/rag-request` (POST+GET) 추가
- Modify: `api/app.py` — `MAX_CONCURRENT_REQUESTS`/`_semaphore`/`limited` 제거
- Replace: `tests/test_api_analyze_endpoint.py` (기존 내용 전체 교체)
- Replace: `tests/test_api_rag_endpoint.py` (기존 내용 전체 교체)
- Modify: `tests/test_api_app.py` — 세마포어 테스트 2개 제거

**Interfaces:**
- Consumes: `scripts.heavy_job_lock`는 여기서 안 씀(API는 가볍게 유지). `analysis.pipeline.list_analyzable_keywords`(기존).
- Produces (Task 5, 8이 사용): `llm_requests` 컬렉션 문서 스키마 —
  ```
  {_id, kind: "analyze"|"rag", keyword, question, sources, status, requested_at, started_at, completed_at, error, result}
  ```
  API 엔드포인트 4개: `POST/GET /api/analyze-request[/<keyword>]`, `POST/GET /api/rag-request[/<job_id>]`.

- [ ] **Step 1: config에 `LLM_REQUESTS_COLLECTION` 추가**

`config/config_cilent.py`에서 Task 3이 추가한 `LOCKS_COLLECTION = "locks"` 다음 줄에 추가:
```python
LLM_REQUESTS_COLLECTION = "llm_requests"          # analyze/rag 비동기 큐 겸 결과 캐시
```

- [ ] **Step 2: 실패하는 테스트 작성 — analyze-request**

`tests/test_api_analyze_endpoint.py`의 기존 내용을 전부 지우고 다음으로 교체:
```python
"""
/api/analyze-request POST/GET 엔드포인트 테스트. Mongo는 fake collection으로 대체.

실행: uv run python tests/test_api_analyze_endpoint.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module
import api.routes as routes
from api.app import create_app


def _patch(target, name, value):
    original = getattr(target, name)
    setattr(target, name, value)
    return original


class _FakeCollection:
    def __init__(self, docs=None):
        self._docs = {d["_id"]: d for d in (docs or [])}

    def find_one(self, filter_):
        doc = self._docs.get(filter_.get("_id"))
        if doc is None:
            return None
        for key, expected in filter_.items():
            if key == "_id":
                continue
            if doc.get(key) != expected:
                return None
        return doc

    def insert_one(self, doc):
        self._docs[doc["_id"]] = doc

    def update_one(self, filter_, update):
        self._docs[filter_["_id"]].update(update["$set"])


def test_keyword_없이_요청하면_400():
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/analyze-request", json={})
    assert resp.status_code == 400, resp.status_code
    print("[OK] keyword 누락 -> 400")


def test_모르는_키워드는_404():
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/analyze-request", json={"keyword": "없는키워드"})
        assert resp.status_code == 404, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.list_analyzable_keywords = original_kw
    print("[OK] 모르는 키워드 -> 404")


def test_신규_요청은_큐에_등록되고_202():
    fake = _FakeCollection()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/analyze-request", json={"keyword": "야르"})
        assert resp.status_code == 202, resp.status_code
        assert resp.get_json() == {"keyword": "야르", "status": "queued"}, resp.get_json()
        doc = fake._docs["야르"]
        assert doc["kind"] == "analyze", doc
        assert doc["status"] == "queued", doc
        assert doc["result"] is None, doc
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 신규 analyze 요청 -> 202 + 큐 등록")


def test_이미_done이면_기존_상태를_그대로_반환한다():
    fake = _FakeCollection([{
        "_id": "야르", "kind": "analyze", "keyword": "야르", "question": None, "sources": None,
        "status": "done", "requested_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc), "completed_at": datetime.now(timezone.utc),
        "error": None, "result": {"result": "분석결과", "sources": [], "trend": None},
    }])
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/analyze-request", json={"keyword": "야르"})
        assert resp.status_code == 202, resp.status_code
        assert resp.get_json() == {"keyword": "야르", "status": "done"}, resp.get_json()
        assert len(fake._docs) == 1, "새 문서를 또 만들면 안 됨(캐시 히트)"
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] done 상태면 캐시 히트로 그대로 반환, 재계산 안 함")


def test_failed_상태는_재요청시_queued로_리셋된다():
    fake = _FakeCollection([{
        "_id": "야르", "kind": "analyze", "keyword": "야르", "question": None, "sources": None,
        "status": "failed", "requested_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "started_at": datetime(2026, 8, 1, tzinfo=timezone.utc), "completed_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "error": "이전 실패", "result": None,
    }])
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/analyze-request", json={"keyword": "야르"})
        assert resp.status_code == 202, resp.status_code
        assert resp.get_json()["status"] == "queued", resp.get_json()
        assert fake._docs["야르"]["error"] is None, fake._docs["야르"]
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] failed -> 재요청 시 queued로 리셋")


def test_GET_요청이력_없으면_404():
    original_get_collection = _patch(routes, "get_collection", lambda name: _FakeCollection())
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/analyze-request/없는키워드")
        assert resp.status_code == 404, resp.status_code
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 요청 이력 없음 -> 404")


def test_GET_완료된_결과를_반환한다():
    fake = _FakeCollection([{
        "_id": "야르", "kind": "analyze", "keyword": "야르", "question": None, "sources": None,
        "status": "done", "requested_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc), "completed_at": datetime.now(timezone.utc),
        "error": None,
        "result": {"result": "분석 결과 텍스트", "sources": [{"title": "제목", "url": "https://example.com"}], "trend": {"status": "유행 중"}},
    }])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/analyze-request/야르")
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["status"] == "done", body
        assert body["result"] == "분석 결과 텍스트", body
        assert body["sources"] == [{"title": "제목", "url": "https://example.com"}], body
        assert body["trend"] == {"status": "유행 중"}, body
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 완료된 analyze 결과 반환")


if __name__ == "__main__":
    test_keyword_없이_요청하면_400()
    test_모르는_키워드는_404()
    test_신규_요청은_큐에_등록되고_202()
    test_이미_done이면_기존_상태를_그대로_반환한다()
    test_failed_상태는_재요청시_queued로_리셋된다()
    test_GET_요청이력_없으면_404()
    test_GET_완료된_결과를_반환한다()
    print("\nALL PASS ✅")
```

- [ ] **Step 3: 실패하는 테스트 작성 — rag-request**

`tests/test_api_rag_endpoint.py`의 기존 내용을 전부 지우고 다음으로 교체:
```python
"""
/api/rag-request POST/GET 엔드포인트 테스트. Mongo는 fake collection으로 대체.

실행: uv run python tests/test_api_rag_endpoint.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module
import api.routes as routes
from api.app import create_app
from api.routes import _rag_job_id


def _patch(target, name, value):
    original = getattr(target, name)
    setattr(target, name, value)
    return original


class _FakeCollection:
    def __init__(self, docs=None):
        self._docs = {d["_id"]: d for d in (docs or [])}

    def find_one(self, filter_):
        doc = self._docs.get(filter_.get("_id"))
        if doc is None:
            return None
        for key, expected in filter_.items():
            if key == "_id":
                continue
            if doc.get(key) != expected:
                return None
        return doc

    def insert_one(self, doc):
        self._docs[doc["_id"]] = doc

    def update_one(self, filter_, update):
        self._docs[filter_["_id"]].update(update["$set"])


def test_job_id는_keyword_question_sources로_결정된다():
    a = _rag_job_id("야르", "무슨 뜻이야?", ["tavily", "youtube"])
    b = _rag_job_id("야르", "무슨 뜻이야?", ["youtube", "tavily"])  # 순서만 다름
    c = _rag_job_id("야르", "다른 질문", ["tavily", "youtube"])
    assert a == b, "sources 순서가 달라도 같은 job_id여야 함"
    assert a != c, "질문이 다르면 job_id도 달라야 함"
    print("[OK] job_id는 keyword+question+sources(순서 무관)로 결정됨")


def test_keyword나_question_누락시_400():
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/rag-request", json={"keyword": "야르"})
    assert resp.status_code == 400, resp.status_code
    print("[OK] question 누락 -> 400")


def test_모르는_키워드는_404():
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/rag-request", json={"keyword": "없는키워드", "question": "질문"})
        assert resp.status_code == 404, resp.status_code
    finally:
        routes.list_analyzable_keywords = original_kw
    print("[OK] 모르는 키워드 -> 404")


def test_신규_요청은_큐에_등록되고_202():
    fake = _FakeCollection()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/rag-request", json={"keyword": "야르", "question": "무슨 뜻이야?"})
        assert resp.status_code == 202, resp.status_code
        body = resp.get_json()
        assert body["status"] == "queued", body
        job_id = body["job_id"]
        doc = fake._docs[job_id]
        assert doc["kind"] == "rag", doc
        assert doc["keyword"] == "야르", doc
        assert doc["question"] == "무슨 뜻이야?", doc
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 신규 rag 요청 -> 202 + 큐 등록")


def test_이미_done이면_기존_상태를_반환하고_새로_안_만든다():
    job_id = _rag_job_id("야르", "무슨 뜻이야?", None)
    fake = _FakeCollection([{
        "_id": job_id, "kind": "rag", "keyword": "야르", "question": "무슨 뜻이야?", "sources": None,
        "status": "done", "requested_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc), "completed_at": datetime.now(timezone.utc),
        "error": None, "result": {"answer": "답변", "sources": [], "trend": None},
    }])
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/rag-request", json={"keyword": "야르", "question": "무슨 뜻이야?"})
        assert resp.status_code == 202, resp.status_code
        assert resp.get_json() == {"job_id": job_id, "status": "done"}, resp.get_json()
        assert len(fake._docs) == 1
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] done 상태면 캐시 히트")


def test_GET_없는_job은_404():
    original_get_collection = _patch(routes, "get_collection", lambda name: _FakeCollection())
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/rag-request/없는job")
        assert resp.status_code == 404, resp.status_code
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 없는 job -> 404")


def test_GET_완료된_결과를_반환한다():
    job_id = _rag_job_id("야르", "무슨 뜻이야?", None)
    fake = _FakeCollection([{
        "_id": job_id, "kind": "rag", "keyword": "야르", "question": "무슨 뜻이야?", "sources": None,
        "status": "done", "requested_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc), "completed_at": datetime.now(timezone.utc),
        "error": None,
        "result": {"answer": "RAG 답변", "sources": [{"title": "제목", "url": "https://example.com"}], "trend": None},
    }])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get(f"/api/rag-request/{job_id}")
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["answer"] == "RAG 답변", body
        assert body["sources"] == [{"title": "제목", "url": "https://example.com"}], body
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 완료된 rag 결과 반환")


if __name__ == "__main__":
    test_job_id는_keyword_question_sources로_결정된다()
    test_keyword나_question_누락시_400()
    test_모르는_키워드는_404()
    test_신규_요청은_큐에_등록되고_202()
    test_이미_done이면_기존_상태를_반환하고_새로_안_만든다()
    test_GET_없는_job은_404()
    test_GET_완료된_결과를_반환한다()
    print("\nALL PASS ✅")
```

- [ ] **Step 4: `tests/test_api_app.py`에서 세마포어 테스트 제거**

`tests/test_api_app.py`에서 `test_한도_내에서는_정상_처리된다`와 `test_세마포어가_꽉_차면_429를_반환한다` 두 함수 정의와, `if __name__ == "__main__":` 블록 안의 두 호출을 제거. 최종 파일:
```python
"""
api/app.py의 앱 팩토리 테스트.
실제 DB/Qdrant/외부 API 호출 없음 — Flask test_client와 더미 라우트로 검증.

실행: uv run python tests/test_api_app.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module


def test_없는_경로는_JSON_404를_반환한다():
    app = app_module.create_app()
    client = app.test_client()
    resp = client.get("/이런경로는없음")
    assert resp.status_code == 404, resp.status_code
    assert resp.get_json() is not None, "404 응답이 JSON이 아님"
    print("[OK] 존재하지 않는 경로 -> JSON 404")


if __name__ == "__main__":
    test_없는_경로는_JSON_404를_반환한다()
    print("\nALL PASS ✅")
```

- [ ] **Step 5: 4개 테스트 파일 실행해 실패 확인**

Run: `uv run python tests/test_api_analyze_endpoint.py && uv run python tests/test_api_rag_endpoint.py`
Expected: `ImportError: cannot import name '_rag_job_id' from 'api.routes'` (아직 라우트를 안 바꿨으므로), 그리고 `/api/analyze-request` 경로 자체가 없어 404(Not Found, JSON 아님)로 실패.

- [ ] **Step 6: 구현 — `api/app.py`에서 세마포어 제거**

`api/app.py` 전체를 다음으로 교체:
```python
"""
app.py
Flask 앱 팩토리.
"""
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)  # 프론트 도메인이 아직 없어 전체 허용. 나중에 도메인이 정해지면 좁힌다.

    from api.routes import bp

    app.register_blueprint(bp)

    @app.errorhandler(Exception)
    def handle_error(exc):
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description}), exc.code
        app.logger.exception("처리되지 않은 예외")
        return jsonify({"error": "서버 내부 오류"}), 500

    return app


app = create_app()
```

- [ ] **Step 7: 구현 — `api/routes.py` 교체**

`api/routes.py` 전체를 다음으로 교체:
```python
"""
routes.py
웹사이트가 호출하는 HTTP API 엔드포인트.

analyze/rag는 더 이상 여기서 직접 계산하지 않는다 — llm_requests 컬렉션에
큐잉/조회만 하고, 실제 임베딩+검색+LLM 호출은 scheduler의
scripts/llm_request_worker.py가 처리한다(docs/superpowers/specs/2026-08-11-analyze-rag-async-perf-design.md).
"""
import hashlib
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from analysis.pipeline import (
    delete_keyword_permanently,
    hide_keyword,
    list_analyzable_keywords,
    list_hidden_keywords,
    list_visible_keywords,
    unhide_keyword,
)
from DB.mongo_client import get_collection
from config.config_cilent import CRAWL_REQUESTS_COLLECTION, LLM_REQUESTS_COLLECTION
from trend.trend_service import get_cached_trend

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@bp.route("/keywords")
def keywords():
    return jsonify({"keywords": list_visible_keywords()})


@bp.route("/keywords/hidden")
def hidden_keywords():
    return jsonify({"keywords": list_hidden_keywords()})


@bp.route("/keywords/<keyword>/hide", methods=["POST"])
def hide_keyword_endpoint(keyword):
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404
    hide_keyword(keyword)
    return jsonify({"keyword": keyword, "hidden": True})


@bp.route("/keywords/<keyword>/unhide", methods=["POST"])
def unhide_keyword_endpoint(keyword):
    unhide_keyword(keyword)
    return jsonify({"keyword": keyword, "hidden": False})


@bp.route("/keywords/<keyword>", methods=["DELETE"])
def delete_keyword_endpoint(keyword):
    if keyword not in list_hidden_keywords():
        return jsonify({"error": "숨긴 키워드만 완전삭제할 수 있습니다. 먼저 숨겨주세요."}), 400
    delete_keyword_permanently(keyword)
    return jsonify({"keyword": keyword, "deleted": True})


@bp.route("/trend/<keyword>")
def trend(keyword):
    result = get_cached_trend(keyword)
    if result is None:
        return jsonify({"error": f"'{keyword}'의 트렌드 데이터가 없습니다"}), 404
    result = {k: v for k, v in result.items() if k != "_id"}
    return jsonify(result)


def _rag_job_id(keyword: str, question: str, sources: list[str] | None) -> str:
    normalized_sources = ",".join(sorted(sources)) if sources else ""
    raw = f"{keyword}|{question}|{normalized_sources}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@bp.route("/analyze-request", methods=["POST"])
def analyze_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword가 필요합니다"}), 400
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404

    collection = get_collection(LLM_REQUESTS_COLLECTION)
    existing = collection.find_one({"_id": keyword})
    if existing and existing["status"] == "failed":
        collection.update_one(
            {"_id": keyword},
            {"$set": {
                "status": "queued", "requested_at": datetime.now(timezone.utc),
                "started_at": None, "completed_at": None, "error": None, "result": None,
            }},
        )
        return jsonify({"keyword": keyword, "status": "queued"}), 202
    if existing:
        return jsonify({"keyword": keyword, "status": existing["status"]}), 202

    collection.insert_one({
        "_id": keyword, "kind": "analyze", "keyword": keyword,
        "question": None, "sources": None,
        "status": "queued", "requested_at": datetime.now(timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    })
    return jsonify({"keyword": keyword, "status": "queued"}), 202


@bp.route("/analyze-request/<keyword>")
def analyze_request_status(keyword):
    doc = get_collection(LLM_REQUESTS_COLLECTION).find_one({"_id": keyword, "kind": "analyze"})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    result = doc.get("result") or {}
    return jsonify({
        "keyword": doc["keyword"],
        "status": doc["status"],
        "result": result.get("result"),
        "sources": result.get("sources"),
        "trend": result.get("trend"),
        "error": doc["error"],
    })


@bp.route("/rag-request", methods=["POST"])
def rag_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    question = (data.get("question") or "").strip()
    if not keyword or not question:
        return jsonify({"error": "keyword와 question이 모두 필요합니다"}), 400
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404

    sources = data.get("sources")  # list[str] | None
    job_id = _rag_job_id(keyword, question, sources)
    collection = get_collection(LLM_REQUESTS_COLLECTION)
    existing = collection.find_one({"_id": job_id})
    if existing and existing["status"] == "failed":
        collection.update_one(
            {"_id": job_id},
            {"$set": {
                "status": "queued", "requested_at": datetime.now(timezone.utc),
                "started_at": None, "completed_at": None, "error": None, "result": None,
            }},
        )
        return jsonify({"job_id": job_id, "status": "queued"}), 202
    if existing:
        return jsonify({"job_id": job_id, "status": existing["status"]}), 202

    collection.insert_one({
        "_id": job_id, "kind": "rag", "keyword": keyword,
        "question": question, "sources": sources,
        "status": "queued", "requested_at": datetime.now(timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    })
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@bp.route("/rag-request/<job_id>")
def rag_request_status(job_id):
    doc = get_collection(LLM_REQUESTS_COLLECTION).find_one({"_id": job_id, "kind": "rag"})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    result = doc.get("result") or {}
    return jsonify({
        "job_id": doc["_id"],
        "status": doc["status"],
        "answer": result.get("answer"),
        "sources": result.get("sources"),
        "trend": result.get("trend"),
        "error": doc["error"],
    })


@bp.route("/crawl-request", methods=["POST"])
def crawl_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword가 필요합니다"}), 400
    if keyword in list_analyzable_keywords():
        return jsonify({"error": "이미 존재하는 키워드입니다"}), 400

    collection = get_collection(CRAWL_REQUESTS_COLLECTION)
    existing = collection.find_one({"_id": keyword})

    if existing and existing["status"] == "failed":
        collection.update_one(
            {"_id": keyword},
            {"$set": {
                "status": "queued",
                "requested_at": datetime.now(timezone.utc),
                "started_at": None,
                "completed_at": None,
                "error": None,
            }},
        )
        return jsonify({"keyword": keyword, "status": "queued"}), 202

    if existing:
        return jsonify({"keyword": keyword, "status": existing["status"]}), 202

    collection.insert_one({
        "_id": keyword,
        "status": "queued",
        "requested_at": datetime.now(timezone.utc),
        "started_at": None,
        "completed_at": None,
        "error": None,
    })
    return jsonify({"keyword": keyword, "status": "queued"}), 202


@bp.route("/crawl-request/<keyword>")
def crawl_request_status(keyword):
    doc = get_collection(CRAWL_REQUESTS_COLLECTION).find_one({"_id": keyword})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    return jsonify({
        "keyword": doc["_id"],
        "status": doc["status"],
        "requested_at": doc["requested_at"].isoformat(),
        "completed_at": doc["completed_at"].isoformat() if doc["completed_at"] else None,
        "error": doc["error"],
    })
```

- [ ] **Step 8: 테스트 실행해 통과 확인**

Run: `uv run python tests/test_api_analyze_endpoint.py && uv run python tests/test_api_rag_endpoint.py && uv run python tests/test_api_app.py && uv run python tests/test_api_keywords_health.py && uv run python tests/test_api_crawl_request_endpoint.py && uv run python tests/test_api_trend_endpoint.py`
Expected: 전부 `ALL PASS ✅` (뒤 3개는 이번 태스크에서 안 건드렸지만 routes.py를 통째로 바꿨으니 회귀 확인 차 같이 돌림)

---

### Task 5: `llm_request_worker.py` — 큐 처리 워커 + scheduler 등록

**Files:**
- Create: `scripts/llm_request_worker.py`
- Modify: `scheduler/scheduler.py` — `llm_request_job` 추가
- Test: `tests/test_llm_request_worker.py` (신규)

**Interfaces:**
- Consumes: Task 3의 `acquire_heavy_job_lock`/`release_heavy_job_lock`, Task 4의 `llm_requests` 스키마·`LLM_REQUESTS_COLLECTION`, 기존 `analysis.pipeline.analyze`, `analysis.query.build_search_query`, `analysis.rag_pipeline.{build_facet_prompt,build_rag_prompt,clean_source_url,default_facet_config,encode_facets,facet_search,search_relevant_chunks}`, `embedding.encoder.encode_batch`, `trend.trend_service.{format_trend_context,get_cached_trend}`
- Produces: `run_once(collection=None) -> None` (scheduler가 `scripts/llm_request_worker.py`를 서브프로세스로 실행할 때 `if __name__ == "__main__": run_once()` 경로로 호출)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_llm_request_worker.py`:
```python
"""
llm_request_worker.run_once() 단위 테스트.
실제 Mongo/Qdrant/임베딩/LLM 없이 fake collection + monkeypatch로 오케스트레이션만 검증.

실행: uv run python tests/test_llm_request_worker.py
"""
import os
import sys
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


def _analyze_doc(status="queued"):
    return {
        "_id": "야르", "kind": "analyze", "keyword": "야르", "question": None, "sources": None,
        "status": status, "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    }


def _rag_doc(status="queued"):
    return {
        "_id": "잡아이디123", "kind": "rag", "keyword": "야르", "question": "무슨 뜻이야?", "sources": ["tavily"],
        "status": status, "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    }


def test_큐가_비어있으면_아무것도_안한다():
    collection = _FakeCollection([])
    original_lock = worker.acquire_heavy_job_lock
    worker.acquire_heavy_job_lock = lambda owner: (_ for _ in ()).throw(AssertionError("호출되면 안 됨"))
    try:
        worker.run_once(collection=collection)
    finally:
        worker.acquire_heavy_job_lock = original_lock
    print("[OK] 빈 큐는 조용히 반환(락도 안 건드림)")


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
    worker.facet_search = lambda keyword, facet_config, facet_vectors=None, is_relevant=None: ([fake_point], {})
    worker.get_cached_trend = lambda keyword: {"status": "유행 중"}
    worker.format_trend_context = lambda keyword, result=None: "트렌드요약"
    worker.build_facet_prompt = lambda keyword, points, trend_info=None: "프롬프트"
    worker.analyze = lambda prompt: calls.setdefault("analyze_prompt", prompt) or "분석 결과"
    worker.clean_source_url = lambda url: url
    try:
        worker.run_once(collection=collection)
        doc = collection._docs["야르"]
        assert doc["status"] == "done", doc
        assert doc["result"]["result"] == "분석 결과", doc
        assert doc["result"]["trend"] == {"status": "유행 중"}, doc
        assert doc["result"]["sources"] == [{"title": "제목", "url": "https://example.com"}], doc
        assert calls["lock_acquired"] == "llm_request_worker", calls
        assert calls["lock_released"] == "llm_request_worker", calls
    finally:
        (worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
         worker.default_facet_config, worker.encode_facets, worker.facet_search,
         worker.get_cached_trend, worker.format_trend_context,
         worker.build_facet_prompt, worker.analyze, worker.clean_source_url) = original
    print("[OK] analyze 작업 정상 처리 -> done + 락 획득/해제")


def test_rag_작업이_정상_처리되면_done으로_바뀐다():
    collection = _FakeCollection([_rag_doc()])
    original = (
        worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
        worker.build_search_query, worker.encode_batch, worker.search_relevant_chunks,
        worker.get_cached_trend, worker.format_trend_context,
        worker.build_rag_prompt, worker.analyze, worker.clean_source_url,
    )
    worker.acquire_heavy_job_lock = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    worker.build_search_query = lambda keyword, question: f"{keyword} {question}"
    worker.encode_batch = lambda texts: ([[0.1, 0.2]], [{}])
    fake_point = SimpleNamespace(payload={"text": "청크", "title": "제목", "url": "https://example.com"})
    worker.search_relevant_chunks = lambda *a, **k: [fake_point]
    worker.get_cached_trend = lambda keyword: None
    worker.format_trend_context = lambda keyword, result=None: "이건호출안됨"
    worker.build_rag_prompt = lambda keyword, question, points, trend_info=None: "프롬프트"
    worker.analyze = lambda prompt: "RAG 답변"
    worker.clean_source_url = lambda url: url
    try:
        worker.run_once(collection=collection)
        doc = collection._docs["잡아이디123"]
        assert doc["status"] == "done", doc
        assert doc["result"]["answer"] == "RAG 답변", doc
        assert doc["result"]["trend"] is None, doc
    finally:
        (worker.acquire_heavy_job_lock, worker.release_heavy_job_lock,
         worker.build_search_query, worker.encode_batch, worker.search_relevant_chunks,
         worker.get_cached_trend, worker.format_trend_context,
         worker.build_rag_prompt, worker.analyze, worker.clean_source_url) = original
    print("[OK] rag 작업 정상 처리 -> done")


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
    worker.facet_search = lambda keyword, facet_config, facet_vectors=None, is_relevant=None: ([], {})
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
        worker.run_once(collection=collection)
        doc = collection._docs["야르"]
        assert doc["status"] == "queued", doc
        assert doc["started_at"] is None, doc
    finally:
        worker.acquire_heavy_job_lock = original_lock
    print("[OK] 락 획득 실패 -> queued로 되돌리고 이번 틱은 skip")


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
    worker.facet_search = lambda keyword, facet_config, facet_vectors=None, is_relevant=None: ([fake_point], {})
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


if __name__ == "__main__":
    test_큐가_비어있으면_아무것도_안한다()
    test_analyze_작업이_정상_처리되면_done으로_바뀐다()
    test_rag_작업이_정상_처리되면_done으로_바뀐다()
    test_결과가_없으면_failed로_기록된다()
    test_락을_못잡으면_다시_queued로_돌리고_반환한다()
    test_예외_발생시_failed와_에러메시지가_기록되고_락이_해제된다()
    test_오래된_running_요청은_requeue된다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run python tests/test_llm_request_worker.py`
Expected: `ModuleNotFoundError: No module named 'scripts.llm_request_worker'`

- [ ] **Step 3: 구현 — `scripts/llm_request_worker.py` 작성**

```python
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
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DB.mongo_client import get_collection
from config.config_cilent import LLM_REQUESTS_COLLECTION
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
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `uv run python tests/test_llm_request_worker.py`
Expected: `ALL PASS ✅`

- [ ] **Step 5: `scheduler/scheduler.py`에 `llm_request_job` 등록**

`scheduler/scheduler.py` 상단(`CRAWL_REQUEST_WORKER_PY` 정의 부근)에 추가:
```python
LLM_REQUEST_WORKER_PY = os.path.join(BASE_DIR, "scripts", "llm_request_worker.py")
```

`crawl_request_run` 함수(70-76행) 다음에 추가 — 로깅은 파일 상단(17행)에 이미 있는 `logger = get_logger("scheduler")`와 `crawl_request_run`이 쓰는 것과 동일한 패턴(성공 로그는 안 남기고 실패만 `logger.error`)을 그대로 따른다:
```python
def llm_request_run():
    """analyze/rag 큐를 1회 확인해서, 있으면 하나 처리한다.
    큐가 비어있으면 llm_request_worker.py 자체가 조용히 종료하므로 여기서
    성공 로그를 남기지 않는다(5초마다 빈 로그가 쌓이는 걸 피하려고) — 실패했을 때만 기록."""
    result = subprocess.run([sys.executable, LLM_REQUEST_WORKER_PY])
    if result.returncode != 0:
        logger.error("analyze/rag 워커 실패 (code=%d)", result.returncode)
```

`scheduler.add_job(crawl_request_run, ...)` 블록 다음에 추가:
```python
scheduler.add_job(
    llm_request_run,
    'interval',
    seconds=5,
    id='llm_request_job',
    coalesce=True,
    misfire_grace_time=30,
    replace_existing=True,
)
```

- [ ] **Step 6: `scheduler/scheduler.py`가 문법 오류 없이 임포트되는지 확인**

Run: `uv run python -c "import scheduler.scheduler"`
Expected: 에러 없이 종료(APScheduler가 백그라운드로 뜨는 코드가 있다면 이 커맨드가 바로 안 끝날 수 있음 — 그 경우 `uv run python -c "import ast; ast.parse(open('scheduler/scheduler.py', encoding='utf-8').read())"`로 문법만 검증)

---

### Task 6: `crawl_request_worker.py`에 `heavy_job_lock` 적용 + `llm_requests` 캐시 무효화

**Files:**
- Modify: `scripts/crawl_request_worker.py`
- Modify: `tests/test_crawl_request_worker.py` — 기존 성공 경로 테스트들에 락/무효화 관련 monkeypatch 추가, 신규 테스트 2개 추가

**Interfaces:**
- Consumes: Task 3의 `acquire_heavy_job_lock_blocking`/`release_heavy_job_lock`, Task 4의 `LLM_REQUESTS_COLLECTION`
- Produces: `run_once(collection=None, llm_requests_collection=None) -> None` (시그니처 변경 — 기존 `collection=None` 하나였던 것에 `llm_requests_collection=None` 파라미터 추가)

- [ ] **Step 1: 구현 — `scripts/crawl_request_worker.py` 수정**

`scripts/crawl_request_worker.py`의 import 블록에 추가:
```python
from config.config_cilent import CRAWL_REQUESTS_COLLECTION, LLM_REQUESTS_COLLECTION
from scripts.heavy_job_lock import acquire_heavy_job_lock_blocking, release_heavy_job_lock
```
(기존 `from config.config_cilent import CRAWL_REQUESTS_COLLECTION` 줄을 위 줄로 교체)

`run_once` 함수 전체를 다음으로 교체:
```python
def run_once(collection=None, llm_requests_collection=None) -> None:
    """큐에서 가장 오래된 queued 요청 하나를 처리. 없으면 즉시 반환."""
    if collection is None:
        collection = get_collection(CRAWL_REQUESTS_COLLECTION)
    if llm_requests_collection is None:
        llm_requests_collection = get_collection(LLM_REQUESTS_COLLECTION)

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

        # crawl_all/preprocess_documents는 이미 끝낸 sunk cost라, 락을 못 잡아도
        # 포기하지 않고 최대 10분까지 기다린다(짧게 한 번 시도하고 포기하는
        # llm_request_worker와 다른 이유: 거긴 사전 작업이 없어 포기 비용이 0).
        if not acquire_heavy_job_lock_blocking("crawl_request_worker"):
            _mark(collection, keyword, "failed", error="임베딩 락 획득 시간 초과(다른 무거운 작업이 오래 실행 중)")
            return
        try:
            embed_result = embed_documents(keyword)
        finally:
            release_heavy_job_lock("crawl_request_worker")

        if embed_result.get("documents", 0) == 0:
            _mark(collection, keyword, "failed", error="수집된 데이터가 없습니다 (모든 소스에서 관련 자료를 찾지 못했습니다)")
        else:
            _mark(collection, keyword, "done")
            llm_requests_collection.delete_many({"keyword": keyword})
    except Exception as e:
        _mark(collection, keyword, "failed", error=str(e))
```

- [ ] **Step 2: 기존 테스트 파일 수정 — 성공 경로에 락 monkeypatch 추가**

`tests/test_crawl_request_worker.py`의 import 블록에 추가:
```python
import scripts.crawl_request_worker as worker
```
다음 줄에 추가(이미 있으면 생략):
```python
from types import SimpleNamespace
```

`_FakeCollection` 클래스 뒤(사용 전이면 어디든)에 헬퍼 추가:
```python
class _FakeLlmRequestsCollection:
    def __init__(self):
        self.deleted_filters = []

    def delete_many(self, filter_):
        self.deleted_filters.append(filter_)
```

다음 4개 테스트 함수를 수정 — 함수 본문에서 `worker.crawl_all = ...` 등을 monkeypatch하는 `try:` 블록 안, `worker.run_once(collection=collection)` 호출 직전에 락 monkeypatch를 추가하고, 호출을 `worker.run_once(collection=collection, llm_requests_collection=fake_llm)`로 바꾼다. 대상 함수: `test_정상_처리시_done으로_바뀐다`, `test_가장_오래된_큐_항목부터_처리한다`, `test_임베딩_결과가_0건이면_failed로_기록된다`, `test_오래된_running_요청은_requeue되어_같은_실행에서_처리된다`.

예를 들어 `test_정상_처리시_done으로_바뀐다`는 다음과 같이 바뀐다:
```python
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

    worker.crawl_all = lambda kws: calls.setdefault("crawl", kws)
    worker.preprocess_documents = lambda kw: calls.setdefault("preprocess", kw)
    worker.embed_documents = fake_embed
    worker.acquire_heavy_job_lock_blocking = lambda owner: True
    worker.release_heavy_job_lock = lambda owner: None
    try:
        worker.run_once(collection=collection, llm_requests_collection=fake_llm)
        assert calls == {"crawl": ["야르"], "preprocess": "야르", "embed": "야르"}, calls
        assert collection._docs["야르"]["status"] == "done", collection._docs["야르"]
        assert fake_llm.deleted_filters == [{"keyword": "야르"}], fake_llm.deleted_filters
    finally:
        worker.crawl_all, worker.preprocess_documents, worker.embed_documents = original_crawl, original_pre, original_embed
        worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock = original_lock, original_release
    print("[OK] 정상 처리 시 done + 3단계 순서대로 호출 + llm_requests 캐시 무효화")
```

나머지 3개 함수도 동일한 패턴으로: `fake_llm = _FakeLlmRequestsCollection()` 추가, `original_lock, original_release = worker.acquire_heavy_job_lock_blocking, worker.release_heavy_job_lock` 저장, `worker.acquire_heavy_job_lock_blocking = lambda owner: True` / `worker.release_heavy_job_lock = lambda owner: None` 설정, `worker.run_once(collection=collection, llm_requests_collection=fake_llm)`로 호출부 변경, `finally`에서 두 함수 복원. (`test_임베딩_결과가_0건이면_failed로_기록된다`는 임베딩이 0건이라 `llm_requests_collection.delete_many`가 호출되지 않아야 하므로 `assert fake_llm.deleted_filters == []`를 추가로 검증.)

- [ ] **Step 3: 신규 테스트 추가 — 락 타임아웃**

`tests/test_crawl_request_worker.py`의 `if __name__ == "__main__":` 블록 위에 추가:
```python
def test_임베딩_락을_못잡으면_failed로_기록되고_임베딩은_호출되지_않는다():
    collection = _FakeCollection([
        {"_id": "야르", "status": "queued", "requested_at": datetime(2026, 8, 10, tzinfo=timezone.utc),
         "started_at": None, "completed_at": None, "error": None},
    ])
    fake_llm = _FakeLlmRequestsCollection()
    calls = {}
    original_crawl, original_pre, original_embed = worker.crawl_all, worker.preprocess_documents, worker.embed_documents
    original_lock = worker.acquire_heavy_job_lock_blocking
    worker.crawl_all = lambda kws: calls.setdefault("crawl", kws)
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
    worker.crawl_all = lambda kws: (_ for _ in ()).throw(RuntimeError("크롤 실패 테스트"))
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
```

`if __name__ == "__main__":` 블록에 두 함수 호출 추가:
```python
    test_임베딩_락을_못잡으면_failed로_기록되고_임베딩은_호출되지_않는다()
    test_예외_발생시_락_함수가_호출되지_않은_경우에도_안전하다()
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `uv run python tests/test_crawl_request_worker.py`
Expected: `ALL PASS ✅`

---

### Task 7: `delete_keyword_permanently`에 `llm_requests` 무효화 추가

**Files:**
- Modify: `analysis/pipeline.py:64-81` (`delete_keyword_permanently` 함수 + 상단 import)
- Test: `tests/test_delete_keyword_permanently.py` (신규)

**Interfaces:**
- Consumes: Task 4의 `LLM_REQUESTS_COLLECTION`
- Produces: `delete_keyword_permanently(keyword)` 시그니처 불변, 부수효과만 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_delete_keyword_permanently.py`:
```python
"""
delete_keyword_permanently()가 llm_requests 컬렉션도 함께 정리하는지 확인.
실제 Mongo/Qdrant 없이 fake collection + monkeypatch로 검증.

실행: uv run python tests/test_delete_keyword_permanently.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis.pipeline as pipeline


class _FakeCollection:
    def __init__(self):
        self.deleted_many_filters = []
        self.deleted_one_filters = []

    def delete_many(self, filter_):
        self.deleted_many_filters.append(filter_)

    def delete_one(self, filter_):
        self.deleted_one_filters.append(filter_)


class _FakeQdrantClient:
    def __init__(self):
        self.delete_calls = []

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


def test_삭제시_llm_requests도_keyword_기준으로_지운다():
    fake_collections = {}

    def fake_get_collection(name=None):
        key = name or "memes"
        fake_collections.setdefault(key, _FakeCollection())
        return fake_collections[key]

    original_get_collection = pipeline.get_collection
    original_client = pipeline.client
    pipeline.get_collection = fake_get_collection
    pipeline.client = _FakeQdrantClient()
    try:
        pipeline.delete_keyword_permanently("야르")
        assert fake_collections["llm_requests"].deleted_many_filters == [{"keyword": "야르"}], \
            fake_collections["llm_requests"].deleted_many_filters
    finally:
        pipeline.get_collection = original_get_collection
        pipeline.client = original_client
    print("[OK] delete_keyword_permanently가 llm_requests도 keyword 기준으로 정리함")


if __name__ == "__main__":
    test_삭제시_llm_requests도_keyword_기준으로_지운다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run python tests/test_delete_keyword_permanently.py`
Expected: `AssertionError` — `fake_collections`에 `"llm_requests"` 키가 없어서 `KeyError`.

- [ ] **Step 3: 구현**

`analysis/pipeline.py`의 import 블록에서:
```python
from config.config_cilent import (
    ANALYSIS_MODEL,
    ANALYSIS_PROMPT_PATH,
    CLEANED_COLLECTION,
    CRAWL_REQUESTS_COLLECTION,
    HIDDEN_KEYWORDS_COLLECTION,
    NIM_KEY,
    QDRANT_COLLECTION,
    TREND_COLLECTION,
)
```
를
```python
from config.config_cilent import (
    ANALYSIS_MODEL,
    ANALYSIS_PROMPT_PATH,
    CLEANED_COLLECTION,
    CRAWL_REQUESTS_COLLECTION,
    HIDDEN_KEYWORDS_COLLECTION,
    LLM_REQUESTS_COLLECTION,
    NIM_KEY,
    QDRANT_COLLECTION,
    TREND_COLLECTION,
)
```
로 변경.

`delete_keyword_permanently` 함수 안, `get_collection(HIDDEN_KEYWORDS_COLLECTION).delete_one({"_id": keyword})` 다음 줄에 추가:
```python
    get_collection(LLM_REQUESTS_COLLECTION).delete_many({"keyword": keyword})
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `uv run python tests/test_delete_keyword_permanently.py`
Expected: `ALL PASS ✅`

---

### Task 8: 프론트 `api.js` — 요청/폴링 함수로 교체

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/api.test.js`

**Interfaces:**
- Consumes: Task 4의 API 계약(`POST/GET /api/analyze-request`, `POST/GET /api/rag-request`)
- Produces (Task 9, 10이 사용):
  - `submitAnalyzeRequest(keyword: string): Promise<{keyword, status}>`
  - `fetchAnalyzeStatus(keyword: string): Promise<{keyword, status, result, sources, trend, error}>`
  - `submitRagRequest(keyword: string, question: string, sources: string[]): Promise<{job_id, status}>`
  - `fetchRagStatus(jobId: string): Promise<{job_id, status, answer, sources, trend, error}>`
  - `analyzeKeyword`, `askRag`는 제거.

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/api.test.js`에서 `analyzeKeyword`/`askRag` 관련 부분(import 목록의 두 이름, `describe('analyzeKeyword', ...)`, `describe('askRag', ...)` 블록)을 지우고, import 목록과 아래 테스트로 교체:

파일 상단 import를:
```javascript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fetchKeywords, fetchTrend, requestCrawl, fetchCrawlStatus, AVAILABLE_SOURCES,
  submitAnalyzeRequest, fetchAnalyzeStatus, submitRagRequest, fetchRagStatus,
} from './api.js'
```
로 바꾸고, 기존 `describe('analyzeKeyword', ...)`와 `describe('askRag', ...)` 블록을 지운 자리에 추가:
```javascript
describe('submitAnalyzeRequest', () => {
  it('POST /api/analyze-request를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keyword: '야르', status: 'queued' }))
    const result = await submitAnalyzeRequest('야르')
    expect(fetch).toHaveBeenCalledWith('/api/analyze-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '야르' }),
    })
    expect(result.status).toBe('queued')
  })

  it('실패 응답이면 에러 메시지를 담은 Error를 던진다', async () => {
    fetch.mockReturnValue(jsonResponse({ error: '데이터를 찾을 수 없습니다' }, false, 404))
    await expect(submitAnalyzeRequest('없는키워드')).rejects.toThrow('데이터를 찾을 수 없습니다')
  })
})

describe('fetchAnalyzeStatus', () => {
  it('GET /api/analyze-request/<keyword>를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({
      keyword: '야르', status: 'done', result: '분석결과', sources: [], trend: null, error: null,
    }))
    const result = await fetchAnalyzeStatus('야르')
    expect(fetch).toHaveBeenCalledWith('/api/analyze-request/야르')
    expect(result.status).toBe('done')
    expect(result.result).toBe('분석결과')
  })
})

describe('submitRagRequest', () => {
  it('sources를 포함해서 POST /api/rag-request를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ job_id: '잡아이디', status: 'queued' }))
    const result = await submitRagRequest('야르', '무슨 뜻이야?', ['tavily'])
    expect(fetch).toHaveBeenCalledWith('/api/rag-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '야르', question: '무슨 뜻이야?', sources: ['tavily'] }),
    })
    expect(result.job_id).toBe('잡아이디')
  })
})

describe('fetchRagStatus', () => {
  it('GET /api/rag-request/<job_id>를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({
      job_id: '잡아이디', status: 'done', answer: '답변', sources: [], trend: null, error: null,
    }))
    const result = await fetchRagStatus('잡아이디')
    expect(fetch).toHaveBeenCalledWith('/api/rag-request/잡아이디')
    expect(result.answer).toBe('답변')
  })
})
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run(작업 디렉터리 `frontend/`): `npm test -- api.test.js`
Expected: `submitAnalyzeRequest` 등이 `undefined`라 실패(아직 `api.js`에 없음).

- [ ] **Step 3: 구현 — `frontend/src/api.js` 수정**

`export async function analyzeKeyword(keyword) { ... }`와 `export async function askRag(keyword, question, sources) { ... }` 두 함수를 지우고 다음으로 교체:
```javascript
export async function submitAnalyzeRequest(keyword) {
  const res = await safeFetch(`${BASE}/analyze-request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword }),
  })
  return handleResponse(res)
}

export async function fetchAnalyzeStatus(keyword) {
  const res = await safeFetch(`${BASE}/analyze-request/${keyword}`)
  return handleResponse(res)
}

export async function submitRagRequest(keyword, question, sources) {
  const res = await safeFetch(`${BASE}/rag-request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword, question, sources }),
  })
  return handleResponse(res)
}

export async function fetchRagStatus(jobId) {
  const res = await safeFetch(`${BASE}/rag-request/${jobId}`)
  return handleResponse(res)
}
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run(`frontend/`): `npm test -- api.test.js`
Expected: 모든 테스트 통과

---

### Task 9: `AnalysisPanel.jsx` — 폴링 방식으로 재작성

**Files:**
- Modify: `frontend/src/components/AnalysisPanel.jsx`
- Modify: `frontend/src/components/AnalysisPanel.test.jsx`

**Interfaces:**
- Consumes: Task 8의 `submitAnalyzeRequest`, `fetchAnalyzeStatus`, 기존 `fetchTrend`(변경 없음)
- Produces: `<AnalysisPanel keyword={string} />` (props 불변, 내부 구현만 폴링으로 전환)

- [ ] **Step 1: 기존 테스트 파일 확인 후 폴링 패턴으로 전체 교체**

먼저 `frontend/src/components/AnalysisPanel.test.jsx`를 읽어서 현재 어떤 걸 테스트하는지 확인할 것(로딩 상태, 에러, 렌더링된 결과 등 — 새 테스트에서도 동등한 항목을 다뤄야 함). 그 다음 파일 전체를 다음으로 교체:
```javascript
import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import AnalysisPanel from './AnalysisPanel.jsx'
import * as api from '../api.js'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('AnalysisPanel', () => {
  it('마운트되면 submitAnalyzeRequest와 fetchTrend를 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)

    render(<AnalysisPanel keyword="야르" />)

    await waitFor(() => expect(submitSpy).toHaveBeenCalledWith('야르'))
  })

  it('status가 done이 되면 결과와 출처를 렌더링하고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue({ status: '유행 중', final_z: 1.2 })
    const fetchStatusSpy = vi.spyOn(api, 'fetchAnalyzeStatus')
      .mockResolvedValueOnce({ keyword: '야르', status: 'running' })
      .mockResolvedValueOnce({
        keyword: '야르', status: 'done', result: '# 분석 결과',
        sources: [{ title: '제목', url: 'https://example.com' }], trend: null, error: null,
      })

    render(<AnalysisPanel keyword="야르" />)

    await vi.advanceTimersByTimeAsync(3000)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText('제목')).toBeInTheDocument()
    const callsAfterDone = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterDone)
  })

  it('status가 failed면 에러와 재시도 버튼을 보여준다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      keyword: '야르', status: 'failed', error: '분석 실패했습니다', result: null, sources: null, trend: null,
    })

    render(<AnalysisPanel keyword="야르" />)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText(/분석 실패했습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })

  it('재시도 버튼을 누르면 submitAnalyzeRequest를 다시 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      keyword: '야르', status: 'failed', error: '분석 실패했습니다', result: null, sources: null, trend: null,
    })

    render(<AnalysisPanel keyword="야르" />)
    await vi.advanceTimersByTimeAsync(3000)
    await screen.findByText(/분석 실패했습니다/)

    screen.getByRole('button', { name: '다시 시도' }).click()
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2))
  })
})
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run(`frontend/`): `npm test -- AnalysisPanel.test.jsx`
Expected: 실패(컴포넌트가 아직 `analyzeKeyword`를 쓰고 있어 `submitAnalyzeRequest`가 호출되지 않음)

- [ ] **Step 3: 구현 — `frontend/src/components/AnalysisPanel.jsx` 재작성**

전체 파일을 다음으로 교체:
```javascript
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchTrend, submitAnalyzeRequest, fetchAnalyzeStatus } from '../api.js'
import TrendGauge from './TrendGauge.jsx'

const POLL_INTERVAL_MS = 3000

function TrendBadge({ trend }) {
  if (!trend) return null
  if (trend.status === '데이터 부족') return <p className="badge badge--none">트렌드: 데이터 부족</p>
  const z = trend.final_z ?? trend.z_score
  return (
    <p className="badge badge--hot">
      트렌드: {trend.status}
      {typeof z === 'number' && ` (z ${z >= 0 ? '+' : ''}${z.toFixed(2)})`}
    </p>
  )
}

export default function AnalysisPanel({ keyword }) {
  const [status, setStatus] = useState('queued')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [sources, setSources] = useState([])
  const [trend, setTrend] = useState(null)
  const [retryCount, setRetryCount] = useState(0)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)

  function startPolling() {
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchAnalyzeStatus(keyword)
        if (cancelledRef.current) return
        setStatus(data.status)
        if (data.status === 'done') {
          clearInterval(timerRef.current)
          setResult(data.result)
          setSources(data.sources || [])
        } else if (data.status === 'failed') {
          clearInterval(timerRef.current)
          setError(data.error)
        }
      } catch (e) {
        if (cancelledRef.current) return
        clearInterval(timerRef.current)
        setError(e.message || '네트워크 오류가 발생했습니다')
      }
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    cancelledRef.current = false
    setStatus('queued')
    setError(null)
    setResult(null)
    setSources([])
    setTrend(null)

    fetchTrend(keyword).then((data) => {
      if (!cancelledRef.current) setTrend(data)
    })

    submitAnalyzeRequest(keyword)
      .then(() => {
        if (!cancelledRef.current) startPolling()
      })
      .catch((e) => {
        if (!cancelledRef.current) setError(e.message || '네트워크 오류가 발생했습니다')
      })

    return () => {
      cancelledRef.current = true
      clearInterval(timerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword, retryCount])

  if (error) {
    return (
      <div className="card card--error">
        <p role="alert" className="alert">{error}</p>
        <button className="btn btn--ghost" onClick={() => setRetryCount((c) => c + 1)}>다시 시도</button>
      </div>
    )
  }

  if (status !== 'done') {
    return (
      <div className="card">
        <p className="loading-line"><span className="spinner" aria-hidden="true" />분석 중...</p>
      </div>
    )
  }

  return (
    <div className="card">
      <TrendBadge trend={trend} />
      <TrendGauge trend={trend} />
      <div className="markdown-body">
        <ReactMarkdown>{result}</ReactMarkdown>
      </div>
      {sources.length > 0 && (
        <ul className="source-list">
          {sources.map((s, i) => (
            <li key={i}>
              <a href={s.url}>{s.title}</a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run(`frontend/`): `npm test -- AnalysisPanel.test.jsx`
Expected: 모든 테스트 통과

---

### Task 10: `RagPanel.jsx` — 폴링 방식으로 재작성

**Files:**
- Modify: `frontend/src/components/RagPanel.jsx`
- Modify: `frontend/src/components/RagPanel.test.jsx`

**Interfaces:**
- Consumes: Task 8의 `submitRagRequest`, `fetchRagStatus`
- Produces: `<RagPanel keyword={string} />` (props 불변)

- [ ] **Step 1: 기존 테스트 파일 확인 후 폴링 패턴으로 전체 교체**

먼저 `frontend/src/components/RagPanel.test.jsx`를 읽어서 현재 테스트 항목을 확인할 것. 그 다음 파일 전체를 다음으로 교체:
```javascript
import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import RagPanel from './RagPanel.jsx'
import * as api from '../api.js'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

async function submitQuestion(user_input = '무슨 뜻이야?') {
  const input = screen.getByLabelText('질문')
  input.focus()
  // jsdom에서 fireEvent 대신 직접 값 설정 + change 이벤트를 쓰지 않고,
  // React Testing Library 관례대로 userEvent를 쓰고 싶다면 이 함수를 그렇게 바꿔도 된다.
}

describe('RagPanel', () => {
  it('질문을 제출하면 submitRagRequest를 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchRagStatus').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })

    render(<RagPanel keyword="야르" />)
    const input = screen.getByLabelText('질문')
    input.value = '무슨 뜻이야?'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    screen.getByRole('button', { name: '질문하기' }).click()

    await waitFor(() => expect(submitSpy).toHaveBeenCalledWith('야르', '무슨 뜻이야?', expect.any(Array)))
  })

  it('status가 done이 되면 답변과 출처를 렌더링하고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    const fetchStatusSpy = vi.spyOn(api, 'fetchRagStatus')
      .mockResolvedValueOnce({ job_id: '잡아이디', status: 'running' })
      .mockResolvedValueOnce({
        job_id: '잡아이디', status: 'done', answer: '답변입니다',
        sources: [{ title: '출처제목', url: 'https://example.com' }], trend: null, error: null,
      })

    render(<RagPanel keyword="야르" />)
    const input = screen.getByLabelText('질문')
    input.value = '무슨 뜻이야?'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    screen.getByRole('button', { name: '질문하기' }).click()

    await vi.advanceTimersByTimeAsync(3000)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText('출처제목')).toBeInTheDocument()
    const callsAfterDone = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterDone)
  })

  it('status가 failed면 에러를 보여준다', async () => {
    vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchRagStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'failed', error: 'RAG 처리 실패했습니다', answer: null, sources: null, trend: null,
    })

    render(<RagPanel keyword="야르" />)
    const input = screen.getByLabelText('질문')
    input.value = '무슨 뜻이야?'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    screen.getByRole('button', { name: '질문하기' }).click()

    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText(/RAG 처리 실패했습니다/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run(`frontend/`): `npm test -- RagPanel.test.jsx`
Expected: 실패(컴포넌트가 아직 `askRag`를 동기로 기다리고 있어 폴링 관련 assertion이 안 맞음)

- [ ] **Step 3: 구현 — `frontend/src/components/RagPanel.jsx` 재작성**

전체 파일을 다음으로 교체:
```javascript
import { useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { submitRagRequest, fetchRagStatus, AVAILABLE_SOURCES } from '../api.js'
import SourceFilter from './SourceFilter.jsx'

const POLL_INTERVAL_MS = 3000

export default function RagPanel({ keyword }) {
  const [selectedSources, setSelectedSources] = useState([...AVAILABLE_SOURCES])
  const [question, setQuestion] = useState('')
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [sources, setSources] = useState([])
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)

  function startPolling(jobId) {
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchRagStatus(jobId)
        if (cancelledRef.current) return
        setStatus(data.status)
        if (data.status === 'done') {
          clearInterval(timerRef.current)
          setAnswer(data.answer)
          setSources(data.sources || [])
        } else if (data.status === 'failed') {
          clearInterval(timerRef.current)
          setError(data.error)
        }
      } catch (e) {
        if (cancelledRef.current) return
        clearInterval(timerRef.current)
        setError(e.message || '네트워크 오류가 발생했습니다')
      }
    }, POLL_INTERVAL_MS)
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!question.trim()) return
    cancelledRef.current = false
    setStatus('queued')
    setError(null)
    setAnswer(null)
    setSources([])
    try {
      const data = await submitRagRequest(keyword, question, selectedSources)
      if (cancelledRef.current) return
      startPolling(data.job_id)
    } catch (e) {
      if (!cancelledRef.current) setError(e.message)
    }
  }

  const loading = status === 'queued' || status === 'running'

  return (
    <div className="card">
      <SourceFilter selected={selectedSources} onChange={setSelectedSources} />
      <form className="form-row" onSubmit={handleSubmit}>
        <label htmlFor="rag-question" className="sr-only">질문</label>
        <input
          id="rag-question"
          className="input"
          aria-label="질문"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="더 궁금한 게 있으세요?"
        />
        <button type="submit" className="btn btn--primary" disabled={loading || selectedSources.length === 0}>질문하기</button>
      </form>
      {selectedSources.length === 0 && <p className="loading-line">최소 하나의 출처를 선택하세요</p>}
      {loading && <p className="loading-line"><span className="spinner" aria-hidden="true" />답변 생성 중...</p>}
      {error && <p role="alert" className="alert">{error}</p>}
      {answer && (
        <div>
          <div className="markdown-body">
            <ReactMarkdown>{answer}</ReactMarkdown>
          </div>
          <ul className="source-list">
            {sources.map((s, i) => (
              <li key={i}>
                <a href={s.url}>{s.title}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run(`frontend/`): `npm test -- RagPanel.test.jsx`
Expected: 모든 테스트 통과

- [ ] **Step 5: 전체 프론트 테스트 스위트로 회귀 확인**

Run(`frontend/`): `npm test`
Expected: 전체 통과

---

## 최종 확인

모든 태스크 완료 후:

- [ ] 백엔드 전체 테스트 실행: 각 `tests/test_*.py` 파일을 `uv run python tests/파일명.py`로 순회 실행(또는 이 repo에 전체 실행 스크립트가 있으면 그것 사용) — 전부 `ALL PASS ✅`
- [ ] 프론트 전체 테스트 실행: `frontend/`에서 `npm test` — 전부 통과
- [ ] `docker-compose.yml`은 이번 작업으로 바꿀 필요 없음(scheduler가 이미 `scripts/*.py`를 서브프로세스로 실행하는 구조라 새 워커도 같은 컨테이너 안에서 그대로 동작). 다만 실제 배포 전에 scheduler 컨테이너가 재시작되어 새 코드를 반영하는지 확인 필요(사용자에게 안내만 하고 직접 배포하지 않음).

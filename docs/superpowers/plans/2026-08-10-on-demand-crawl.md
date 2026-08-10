# 새 키워드 온디맨드 수집 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 사용자가 데이터 없는 새 키워드를 요청하면, `crawl_requests` 큐에 등록되고 scheduler가 백그라운드에서 크롤링→정제→임베딩을 이어서 처리해 몇 분 뒤 자동으로 분석 가능해지게 만든다.

**Architecture:** API는 큐에 문서 하나 넣고 즉시 응답(가벼움). scheduler 컨테이너가 1분마다 큐를 확인하는 새 APScheduler 작업으로 실제 무거운 작업(크롤링+정제+임베딩)을 서브프로세스로 수행 — 기존 `crawlrun`/`preprocess_embed_run` 패턴 그대로 재사용.

**Tech Stack:** Python(기존 스택 그대로), pymongo, APScheduler(이미 사용 중), 기존 `crawl_all`/`preprocess_documents`/`embed_documents` 재사용.

## Global Constraints

- 참고 스펙: `docs/superpowers/specs/2026-08-10-on-demand-crawl-design.md`
- `crawl_requests` 컬렉션 스키마: `{_id: keyword, status: "queued"|"running"|"done"|"failed", requested_at, started_at, completed_at, error}`
- 동시 처리 1개 제한은 APScheduler의 `max_instances` 기본값(1)에 맡긴다 — 별도 락 코드 작성 금지(YAGNI)
- `POST /api/crawl-request`는 `/api/analyze`/`/api/rag`와 달리 `@limited` 데코레이터를 붙이지 않는다(가벼운 Mongo 읽기/쓰기라 세마포어 대상 아님)
- `failed` 상태는 재요청 시 `queued`로 리셋(멱등성의 유일한 예외) — 그 외 기존 상태(`queued`/`running`/`done`)는 재요청해도 기존 상태 그대로 반환
- 테스트는 이 프로젝트의 기존 컨벤션을 따른다: pytest 없이 `def test_*()` + `if __name__ == "__main__":`에서 순서대로 호출 + 끝에 `print("ALL PASS ✅")`. 실행은 `PYTHONIOENCODING=utf-8 uv run python tests/test_X.py`.
- Python 버전 제약(기존 `pyproject.toml`): `>=3.11,<3.12`

---

## File Structure

```
config/config_cilent.py         # 수정 — CRAWL_REQUESTS_COLLECTION 상수 추가
scripts/crawl_request_worker.py # 신규 — scheduler가 서브프로세스로 호출하는 큐 처리 워커
api/routes.py                    # 수정 — POST /api/crawl-request, GET /api/crawl-request/<keyword>
scheduler/scheduler.py           # 수정 — crawl_request_run() + 새 주기 작업 등록
tests/test_crawl_request_worker.py
tests/test_api_crawl_request_endpoint.py
```

---

### Task 1: 큐 처리 워커 (`scripts/crawl_request_worker.py`)

**Files:**
- Modify: `config/config_cilent.py` (상수 1줄 추가)
- Create: `scripts/crawl_request_worker.py`
- Test: `tests/test_crawl_request_worker.py`

**Interfaces:**
- Consumes: `DB.mongo_client.get_collection(name) -> Collection`(기존), `main.crawl_all(keywords: list[str]) -> dict`(기존), `preprocessing.pipeline.preprocess_documents(keyword) -> list[dict]`(기존), `embedding.pipeline.embed_documents(keyword) -> dict`(기존)
- Produces: `run_once(collection=None) -> None` — `collection`을 주면 그걸 쓰고(테스트용), 생략하면 실제 `crawl_requests` 컬렉션을 씀. 큐가 비어있으면 조용히 반환.

- [ ] **Step 1: `config/config_cilent.py`에 상수 추가**

`TREND_COLLECTION = "trend_scores"` 줄 근처에 추가:

```python
CRAWL_REQUESTS_COLLECTION = "crawl_requests"     # 새 키워드 온디맨드 수집 큐
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_crawl_request_worker.py`:

```python
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
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_crawl_request_worker.py`
Expected: `ModuleNotFoundError: No module named 'scripts.crawl_request_worker'`

- [ ] **Step 4: `scripts/crawl_request_worker.py` 작성**

```python
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
from datetime import datetime, timezone

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


def run_once(collection=None) -> None:
    """큐에서 가장 오래된 queued 요청 하나를 처리. 없으면 즉시 반환."""
    if collection is None:
        collection = get_collection(CRAWL_REQUESTS_COLLECTION)

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
        embed_documents(keyword)
        _mark(collection, keyword, "done")
    except Exception as e:
        _mark(collection, keyword, "failed", error=str(e))


if __name__ == "__main__":
    run_once()
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_crawl_request_worker.py`
Expected: `ALL PASS ✅`

- [ ] **Step 6: 커밋**

```bash
git add config/config_cilent.py scripts/crawl_request_worker.py tests/test_crawl_request_worker.py
git commit -m "feat(crawl): 온디맨드 키워드 수집 큐 처리 워커 추가"
```

---

### Task 2: API 엔드포인트 (`api/routes.py`)

**Files:**
- Modify: `api/routes.py`
- Test: `tests/test_api_crawl_request_endpoint.py`

**Interfaces:**
- Consumes: `analysis.pipeline.list_analyzable_keywords() -> list[str]`(기존), `DB.mongo_client.get_collection`(기존), `config.config_cilent.CRAWL_REQUESTS_COLLECTION`(Task 1)
- Produces: `POST /api/crawl-request {"keyword": str} -> 202 {"keyword","status"}` / `400`, `GET /api/crawl-request/<keyword> -> 200 {...}` / `404`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_api_crawl_request_endpoint.py`:

```python
"""
/api/crawl-request POST/GET 엔드포인트 테스트. Mongo는 fake collection으로 대체.

실행: uv run python tests/test_api_crawl_request_endpoint.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        return self._docs.get(filter_["_id"])

    def insert_one(self, doc):
        self._docs[doc["_id"]] = doc

    def update_one(self, filter_, update):
        self._docs[filter_["_id"]].update(update["$set"])


def test_이미_있는_키워드는_400():
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: _FakeCollection())
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/crawl-request", json={"keyword": "야르"})
        assert resp.status_code == 400, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 이미 존재하는 키워드 -> 400")


def test_새_키워드는_큐에_등록되고_202():
    fake = _FakeCollection()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: [])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/crawl-request", json={"keyword": "흘로망"})
        assert resp.status_code == 202, resp.status_code
        body = resp.get_json()
        assert body == {"keyword": "흘로망", "status": "queued"}, body
        assert fake._docs["흘로망"]["status"] == "queued"
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 신규 키워드 -> 202 + 큐 등록")


def test_이미_큐에_있으면_기존_상태를_반환한다():
    fake = _FakeCollection([{
        "_id": "흘로망", "status": "running", "requested_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc), "completed_at": None, "error": None,
    }])
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: [])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/crawl-request", json={"keyword": "흘로망"})
        assert resp.status_code == 202, resp.status_code
        assert resp.get_json() == {"keyword": "흘로망", "status": "running"}
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 이미 큐에 있으면 새로 안 만들고 기존 상태 반환")


def test_failed_상태는_재요청시_queued로_리셋된다():
    fake = _FakeCollection([{
        "_id": "흘로망", "status": "failed", "requested_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "started_at": datetime(2026, 8, 1, tzinfo=timezone.utc), "completed_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "error": "이전 실패",
    }])
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: [])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/crawl-request", json={"keyword": "흘로망"})
        assert resp.status_code == 202, resp.status_code
        assert resp.get_json()["status"] == "queued", resp.get_json()
        assert fake._docs["흘로망"]["status"] == "queued"
        assert fake._docs["흘로망"]["error"] is None
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] failed -> 재요청 시 queued로 리셋")


def test_keyword_없이_요청하면_400():
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/crawl-request", json={})
    assert resp.status_code == 400, resp.status_code
    print("[OK] keyword 누락 -> 400")


def test_GET_요청이력_없으면_404():
    original_get_collection = _patch(routes, "get_collection", lambda name: _FakeCollection())
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/crawl-request/없는키워드")
        assert resp.status_code == 404, resp.status_code
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 요청 이력 없음 -> 404")


def test_GET_상태_조회():
    fake = _FakeCollection([{
        "_id": "야르", "status": "done", "requested_at": datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
        "started_at": datetime(2026, 8, 10, 9, 1, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 8, 10, 9, 20, tzinfo=timezone.utc), "error": None,
    }])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/crawl-request/야르")
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["keyword"] == "야르", body
        assert body["status"] == "done", body
        assert body["completed_at"] == "2026-08-10T09:20:00+00:00", body
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 상태 조회 정상")


if __name__ == "__main__":
    test_이미_있는_키워드는_400()
    test_새_키워드는_큐에_등록되고_202()
    test_이미_큐에_있으면_기존_상태를_반환한다()
    test_failed_상태는_재요청시_queued로_리셋된다()
    test_keyword_없이_요청하면_400()
    test_GET_요청이력_없으면_404()
    test_GET_상태_조회()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_api_crawl_request_endpoint.py`
Expected: `404 NOT FOUND` (라우트 없음) 또는 `AttributeError: module 'api.routes' has no attribute 'get_collection'`

- [ ] **Step 3: `api/routes.py` 수정**

import 블록에 추가:

```python
from datetime import datetime, timezone

from DB.mongo_client import get_collection
from config.config_cilent import CRAWL_REQUESTS_COLLECTION
```

파일 끝에 추가:

```python
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

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_api_crawl_request_endpoint.py`
Expected: `ALL PASS ✅`

- [ ] **Step 5: 기존 API 테스트 회귀 확인**

Run:
```bash
PYTHONIOENCODING=utf-8 uv run python tests/test_api_app.py
PYTHONIOENCODING=utf-8 uv run python tests/test_api_keywords_health.py
PYTHONIOENCODING=utf-8 uv run python tests/test_api_analyze_endpoint.py
PYTHONIOENCODING=utf-8 uv run python tests/test_api_rag_endpoint.py
PYTHONIOENCODING=utf-8 uv run python tests/test_api_trend_endpoint.py
```
Expected: 전부 `ALL PASS ✅`

- [ ] **Step 6: 커밋**

```bash
git add api/routes.py tests/test_api_crawl_request_endpoint.py
git commit -m "feat(api): POST/GET /api/crawl-request 엔드포인트"
```

---

### Task 3: scheduler 주기 작업 등록 (`scheduler/scheduler.py`)

**Files:**
- Modify: `scheduler/scheduler.py`

**Interfaces:**
- Consumes: `scripts/crawl_request_worker.py`(Task 1, 서브프로세스로 실행)
- Produces: 없음(마지막 태스크, 배포 설정 성격)

이 태스크는 `scheduler.py` 자체가 모듈 최상단에서 `scheduler.start()`를 실행하는 부작용이 있어(기존 관례) 자동 테스트 대상이 아니다 — 기존 `crawlrun`/`preprocess_embed_run`도 별도 테스트 파일이 없다. 수동 검증으로 마무리한다.

- [ ] **Step 1: `scheduler/scheduler.py`에 경로 상수 추가**

`PREPROCESS_EMBED_PY = os.path.join(BASE_DIR, "preprocess_embed_main.py")` 줄 아래에 추가:

```python
CRAWL_REQUEST_WORKER_PY = os.path.join(BASE_DIR, "scripts", "crawl_request_worker.py")
```

- [ ] **Step 2: 실행 함수 추가**

`preprocess_embed_run()` 함수 아래에 추가:

```python
def crawl_request_run():
    """온디맨드 키워드 수집 큐를 1회 확인해서, 있으면 하나 처리한다.
    큐가 비어있으면 crawl_request_worker.py 자체가 조용히 종료하므로 여기서
    성공 로그를 남기지 않는다(1분마다 빈 로그가 쌓이는 걸 피하려고) — 실패했을 때만 기록."""
    result = subprocess.run([sys.executable, CRAWL_REQUEST_WORKER_PY])
    if result.returncode != 0:
        logger.error("온디맨드 수집 워커 실패 (code=%d)", result.returncode)
```

- [ ] **Step 3: 주기 작업 등록**

`scheduler.add_job(heartbeat, ...)` 블록 위 또는 아래(다른 `add_job` 호출들 옆)에 추가:

```python
scheduler.add_job(
    crawl_request_run,
    'interval',
    minutes=1,
    id='crawl_request_job',
    coalesce=True,
    misfire_grace_time=60,
    replace_existing=True,
)
```

- [ ] **Step 4: 문법 오류 없는지 확인**

Run: `PYTHONIOENCODING=utf-8 uv run python -c "import ast; ast.parse(open('scheduler/scheduler.py', encoding='utf-8').read())"`
Expected: 에러 없이 종료

- [ ] **Step 5: 커밋**

```bash
git add scheduler/scheduler.py
git commit -m "feat(scheduler): 온디맨드 수집 큐 처리 주기 작업(1분) 등록"
```

- [ ] **Step 6: 수동 검증 절차 기록 (배포 후 실제로 확인할 것)**

이 태스크는 로컬 자동 테스트로 끝까지 검증할 수 없다(실제 Mongo/크롤러/scheduler 프로세스 필요). 배포 후 아래로 확인:

```bash
# EC2에서, 테스트용 키워드로 큐에 하나 넣어보고
curl -X POST http://localhost:5000/api/crawl-request -H "Content-Type: application/json" -d '{"keyword":"테스트키워드"}'

# 1~2분 뒤 scheduler 로그에서 처리 시작하는지 확인
docker compose logs scheduler --tail 20

# 상태 폴링
curl http://localhost:5000/api/crawl-request/테스트키워드
```

---

## 스펙 커버리지 체크 (self-review)

- ✅ `crawl_requests` 스키마, 큐잉/처리/상태 4단계 — Task 1, 2
- ✅ APScheduler `max_instances` 기본값으로 동시 1개 제한 — Task 3(코드 안 씀, 설계 자체가 그 특성 이용)
- ✅ `POST`가 `@limited` 대상 아님 — Task 2에서 데코레이터 안 붙임
- ✅ `failed` → 재요청 시 `queued` 리셋 — Task 2 테스트로 검증
- ✅ 기존 `crawl_all`/`preprocess_documents`/`embed_documents` 재사용, 새 로직 없음 — Task 1

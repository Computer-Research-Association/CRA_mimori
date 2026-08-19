"""
/api/crawl-request POST/GET 엔드포인트 테스트. Mongo는 fake collection으로 대체.

실행: uv run python tests/test_api_crawl_request_endpoint.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module
import api.routes as routes
from api.app import create_app
from api.rate_limit import reset as reset_rate_limit


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

    def find_one_and_update(self, filter_, update, upsert=False, return_document=None):
        """실제 Mongo의 upsert + $setOnInsert + return_document=AFTER를 흉내낸다.
        문서가 없으면 $setOnInsert로 새로 만들고, 있으면 손대지 않은 채(둘 다
        $setOnInsert만 쓰는 호출부 기준) 그대로 돌려준다 — find_one_and_update로
        "확인+삽입"을 원자적으로 묶은 실제 동작을 검증하는 게 목적."""
        doc_id = filter_["_id"]
        existing = self._docs.get(doc_id)
        if existing is not None:
            if "$set" in update:
                existing.update(update["$set"])
            return existing
        if not upsert:
            return None
        new_doc = {"_id": doc_id, **update.get("$setOnInsert", {}), **update.get("$set", {})}
        self._docs[doc_id] = new_doc
        return new_doc


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


def test_문장형_키워드는_큐에_안_들어가고_400():
    reset_rate_limit()
    fake = _FakeCollection()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: [])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/crawl-request", json={"keyword": "언어 치료사 1급 합격 방법"})
        assert resp.status_code == 400, resp.status_code
        assert "error" in resp.get_json()
        assert fake._docs == {}, "문장형 키워드가 큐에 등록되면 안 됨"
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 문장형 질문 -> 큐에 안 들어가고 즉시 400")


def test_같은_키워드로_거의_동시에_두_번_요청해도_에러_없이_같은_결과를_돌려준다():
    """React StrictMode의 effect 이중 실행 등으로 같은 키워드가 짧은 시간 안에
    두 번 요청될 수 있다. find_one 후 insert_one을 따로 하던 예전 방식은 둘 다
    "없음"을 보고 둘 다 삽입을 시도해 DuplicateKeyError로 500이 났다.
    find_one_and_update(upsert=True) 덕분에 두 번째 호출도 크래시 없이 첫
    호출과 같은 결과(queued)를 받아야 한다."""
    reset_rate_limit()
    fake = _FakeCollection()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: [])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp1 = client.post("/api/crawl-request", json={"keyword": "흘로망"})
        resp2 = client.post("/api/crawl-request", json={"keyword": "흘로망"})
        assert resp1.status_code == 202, resp1.status_code
        assert resp2.status_code == 202, resp2.status_code
        assert resp1.get_json() == {"keyword": "흘로망", "status": "queued"}
        assert resp2.get_json() == {"keyword": "흘로망", "status": "queued"}
        assert len(fake._docs) == 1, "중복 삽입 없이 문서가 하나만 있어야 함"
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 같은 키워드 연속 요청 -> 에러 없이 동일한 queued 응답, 문서는 하나")


def test_keyword에_개행이_있으면_400():
    reset_rate_limit()
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/crawl-request", json={"keyword": "야르\n쌰갈"})
    assert resp.status_code == 400, resp.status_code
    assert "제어 문자" in resp.get_json()["error"]
    print("[OK] keyword에 개행 -> 400 (Keywords.md 줄 오염 방지)")


def test_keyword가_너무_길면_400():
    reset_rate_limit()
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/crawl-request", json={"keyword": "가" * 51})
    assert resp.status_code == 400, resp.status_code
    print("[OK] keyword 50자 초과 -> 400")


def test_분당_상한을_넘으면_429():
    reset_rate_limit()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: [])
    original_get_collection = _patch(routes, "get_collection", lambda name: _FakeCollection())
    try:
        app = create_app()
        client = app.test_client()
        for i in range(5):
            resp = client.post("/api/crawl-request", json={"keyword": f"키워드{i}"})
            assert resp.status_code == 202, (i, resp.status_code)
        resp = client.post("/api/crawl-request", json={"keyword": "여섯번째"})
        assert resp.status_code == 429, resp.status_code
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
        reset_rate_limit()  # 이 테스트가 상한을 다 써버렸으니 뒤에 오는 테스트로 새지 않게 정리
    print("[OK] 분당 5회 초과 -> 429")


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
    test_문장형_키워드는_큐에_안_들어가고_400()
    test_같은_키워드로_거의_동시에_두_번_요청해도_에러_없이_같은_결과를_돌려준다()
    test_keyword에_개행이_있으면_400()
    test_keyword가_너무_길면_400()
    test_분당_상한을_넘으면_429()
    test_GET_요청이력_없으면_404()
    test_GET_상태_조회()
    print("\nALL PASS ✅")

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
    test_keyword에_개행이_있으면_400()
    test_keyword가_너무_길면_400()
    test_분당_상한을_넘으면_429()
    test_GET_요청이력_없으면_404()
    test_GET_상태_조회()
    print("\nALL PASS ✅")

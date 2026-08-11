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

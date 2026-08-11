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

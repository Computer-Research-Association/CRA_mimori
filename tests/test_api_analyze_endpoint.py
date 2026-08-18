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
from api.routes import _analyze_job_id
from api.rate_limit import reset as reset_rate_limit


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


def test_job_id는_keyword와_sources로_결정된다():
    a = _analyze_job_id("야르", ["tavily", "youtube"])
    b = _analyze_job_id("야르", ["youtube", "tavily"])  # 순서만 다름
    c = _analyze_job_id("야르", ["tavily"])
    d = _analyze_job_id("야르", None)
    assert a == b, "sources 순서가 달라도 같은 job_id여야 함"
    assert a != c, "출처 목록이 다르면 job_id도 달라야 함"
    assert a != d, "출처 생략과 전체 지정은 다른 job_id여야 함"
    print("[OK] job_id는 keyword+sources(순서 무관)로 결정됨")


def test_keyword_없이_요청하면_400():
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/analyze-request", json={})
    assert resp.status_code == 400, resp.status_code
    print("[OK] keyword 누락 -> 400")


def test_keyword에_개행이_있으면_400():
    reset_rate_limit()
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/analyze-request", json={"keyword": "야르\n쌰갈"})
    assert resp.status_code == 400, resp.status_code
    assert "제어 문자" in resp.get_json()["error"]
    print("[OK] keyword에 개행 -> 400")


def test_keyword가_너무_길면_400():
    reset_rate_limit()
    app = create_app()
    client = app.test_client()
    resp = client.post("/api/analyze-request", json={"keyword": "가" * 51})
    assert resp.status_code == 400, resp.status_code
    print("[OK] keyword 50자 초과 -> 400")


def test_분당_상한을_넘으면_429():
    reset_rate_limit()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: _FakeCollection())
    try:
        app = create_app()
        client = app.test_client()
        for i in range(20):
            resp = client.post("/api/analyze-request", json={"keyword": "야르", "sources": [f"s{i}"]})
            assert resp.status_code == 202, (i, resp.status_code)
        resp = client.post("/api/analyze-request", json={"keyword": "야르", "sources": ["마지막"]})
        assert resp.status_code == 429, resp.status_code
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
        reset_rate_limit()  # 이 테스트가 상한을 다 써버렸으니 뒤에 오는 테스트로 새지 않게 정리
    print("[OK] 분당 20회 초과 -> 429")


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
        resp = client.post("/api/analyze-request", json={"keyword": "야르", "sources": ["tavily", "youtube"]})
        assert resp.status_code == 202, resp.status_code
        body = resp.get_json()
        assert body["status"] == "queued", body
        job_id = body["job_id"]
        doc = fake._docs[job_id]
        assert doc["keyword"] == "야르", doc
        assert doc["sources"] == ["tavily", "youtube"], doc
        assert doc["status"] == "queued", doc
        assert doc["result"] is None, doc
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 신규 analyze 요청 -> 202 + 큐 등록")


def test_출처가_다르면_다른_job으로_큐잉된다():
    fake = _FakeCollection()
    original_kw = _patch(routes, "list_analyzable_keywords", lambda: ["야르"])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp_all = client.post("/api/analyze-request", json={"keyword": "야르"})
        resp_narrow = client.post("/api/analyze-request", json={"keyword": "야르", "sources": ["tavily"]})
        assert resp_all.get_json()["job_id"] != resp_narrow.get_json()["job_id"]
        assert len(fake._docs) == 2, "출처가 다르면 별개 캐시 슬롯을 써야 함"
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] 출처가 다르면 캐시가 섞이지 않고 별도 job으로 처리됨")


def test_이미_done이면_기존_상태를_그대로_반환한다():
    job_id = _analyze_job_id("야르", None)
    fake = _FakeCollection([{
        "_id": job_id, "keyword": "야르", "sources": None,
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
        assert resp.get_json() == {"job_id": job_id, "status": "done"}, resp.get_json()
        assert len(fake._docs) == 1, "새 문서를 또 만들면 안 됨(캐시 히트)"
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] done 상태면 캐시 히트로 그대로 반환, 재계산 안 함")


def test_failed_상태는_재요청시_queued로_리셋된다():
    job_id = _analyze_job_id("야르", None)
    fake = _FakeCollection([{
        "_id": job_id, "keyword": "야르", "sources": None,
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
        assert fake._docs[job_id]["error"] is None, fake._docs[job_id]
    finally:
        routes.list_analyzable_keywords = original_kw
        routes.get_collection = original_get_collection
    print("[OK] failed -> 재요청 시 queued로 리셋")


def test_GET_요청이력_없으면_404():
    original_get_collection = _patch(routes, "get_collection", lambda name: _FakeCollection())
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/analyze-request/없는job")
        assert resp.status_code == 404, resp.status_code
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 요청 이력 없음 -> 404")


def test_GET_완료된_결과를_반환한다():
    job_id = _analyze_job_id("야르", None)
    fake = _FakeCollection([{
        "_id": job_id, "keyword": "야르", "sources": None,
        "status": "done", "requested_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc), "completed_at": datetime.now(timezone.utc),
        "error": None,
        "result": {"result": "분석 결과 텍스트", "sources": [{"title": "제목", "url": "https://example.com"}], "trend": {"status": "유행 중"}},
    }])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get(f"/api/analyze-request/{job_id}")
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["job_id"] == job_id, body
        assert body["keyword"] == "야르", body
        assert body["status"] == "done", body
        assert body["result"] == "분석 결과 텍스트", body
        assert body["sources"] == [{"title": "제목", "url": "https://example.com"}], body
        assert body["trend"] == {"status": "유행 중"}, body
        assert body["partial_result"] is None, "완료된 결과엔 partial_text가 없으므로 None이어야 함"
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 완료된 analyze 결과 반환")


def test_GET_처리중이면_partial_result를_반환한다():
    job_id = _analyze_job_id("야르", None)
    fake = _FakeCollection([{
        "_id": job_id, "keyword": "야르", "sources": None,
        "status": "running", "requested_at": datetime.now(timezone.utc),
        "started_at": datetime.now(timezone.utc), "completed_at": None,
        "error": None,
        "result": {
            "sources": [{"title": "제목", "url": "https://example.com"}],
            "trend": {"status": "유행 중"},
            "partial_text": "지금까지 생성된 답변...",
        },
    }])
    original_get_collection = _patch(routes, "get_collection", lambda name: fake)
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get(f"/api/analyze-request/{job_id}")
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["status"] == "running", body
        assert body["result"] is None, "아직 완료 전이므로 최종 result는 None이어야 함"
        assert body["sources"] == [{"title": "제목", "url": "https://example.com"}], body
        assert body["partial_result"] == "지금까지 생성된 답변...", body
    finally:
        routes.get_collection = original_get_collection
    print("[OK] GET 처리중(running) -> 조기 노출된 sources/partial_result 반환")


if __name__ == "__main__":
    test_job_id는_keyword와_sources로_결정된다()
    test_keyword_없이_요청하면_400()
    test_keyword에_개행이_있으면_400()
    test_keyword가_너무_길면_400()
    test_분당_상한을_넘으면_429()
    test_모르는_키워드는_404()
    test_신규_요청은_큐에_등록되고_202()
    test_출처가_다르면_다른_job으로_큐잉된다()
    test_이미_done이면_기존_상태를_그대로_반환한다()
    test_failed_상태는_재요청시_queued로_리셋된다()
    test_GET_요청이력_없으면_404()
    test_GET_완료된_결과를_반환한다()
    test_GET_처리중이면_partial_result를_반환한다()
    print("\nALL PASS ✅")

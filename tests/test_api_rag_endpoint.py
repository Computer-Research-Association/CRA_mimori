"""
/api/rag 엔드포인트 테스트. 임베딩/Qdrant/LLM 호출은 전부 monkeypatch로 대체.

실행: uv run python tests/test_api_rag_endpoint.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module
import api.routes as routes


def _patch(target, name, value):
    original = getattr(target, name)
    setattr(target, name, value)
    return original


def test_keyword나_question_누락시_400():
    app = app_module.create_app()
    client = app.test_client()
    resp = client.post("/api/rag", json={"keyword": "야르"})  # question 없음
    assert resp.status_code == 400, resp.status_code
    print("[OK] question 누락 -> 400")


def test_검색결과_없으면_404():
    original_encode = _patch(routes, "encode_batch", lambda texts: ([[0.1, 0.2]], [{}]))
    original_search = _patch(
        routes, "search_relevant_chunks", lambda *a, **k: []
    )
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/rag", json={"keyword": "야르", "question": "무슨 뜻이야?"})
        assert resp.status_code == 404, resp.status_code
    finally:
        routes.encode_batch = original_encode
        routes.search_relevant_chunks = original_search
    print("[OK] 검색 결과 없음 -> 404")


def test_정상_흐름은_200과_답변_출처를_반환한다():
    fake_point = SimpleNamespace(
        payload={"title": "야르 뜻 정리", "url": "https://example.com/야르", "text": "본문"}
    )
    calls = {}

    original_encode = _patch(routes, "encode_batch", lambda texts: ([[0.1, 0.2]], [{}]))
    original_search = _patch(
        routes, "search_relevant_chunks", lambda *a, **k: [fake_point]
    )
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: None)
    original_ctx = _patch(
        routes, "format_trend_context", lambda keyword, result=None: "트렌드요약"
    )

    def fake_build_rag_prompt(keyword, question, points, trend_info=None):
        calls["build_rag_prompt"] = (keyword, question, len(points), trend_info)
        return "완성된프롬프트"

    def fake_analyze(prompt):
        calls["analyze_prompt"] = prompt
        return "RAG 답변 텍스트"

    original_build = _patch(routes, "build_rag_prompt", fake_build_rag_prompt)
    original_analyze = _patch(routes, "analyze", fake_analyze)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post(
            "/api/rag", json={"keyword": "야르", "question": "무슨 뜻이야?"}
        )
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["answer"] == "RAG 답변 텍스트", body
        assert body["sources"] == [
            {"title": "야르 뜻 정리", "url": "https://example.com/야르"}
        ], body
        # get_cached_trend가 None이면 trend_info는 "" (실시간 호출로 새지 않아야 함)
        assert calls["build_rag_prompt"] == ("야르", "무슨 뜻이야?", 1, ""), calls
        # trend는 None이어야 함
        assert body["trend"] is None, body
    finally:
        routes.encode_batch = original_encode
        routes.search_relevant_chunks = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_rag_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] 정상 흐름 200 + 출처 목록 + trend_info='' 확인 + trend=None")


def test_trend가_있을_때_id는_제외된다():
    """Regression: trend response에서 _id 필드를 제외하는지 확인"""
    fake_point = SimpleNamespace(
        payload={"title": "야르 뜻 정리", "url": "https://example.com/야르", "text": "본문"}
    )
    fake_trend = {
        "_id": "ObjectId('12345')",  # 이 필드는 응답에서 제외되어야 함
        "keyword": "야르",
        "mention_count": 100,
    }
    calls = {}

    original_encode = _patch(routes, "encode_batch", lambda texts: ([[0.1, 0.2]], [{}]))
    original_search = _patch(
        routes, "search_relevant_chunks", lambda *a, **k: [fake_point]
    )
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: fake_trend)
    original_ctx = _patch(
        routes, "format_trend_context", lambda keyword, result=None: "트렌드요약"
    )

    def fake_build_rag_prompt(keyword, question, points, trend_info=None):
        calls["build_rag_prompt"] = (keyword, question, len(points), trend_info)
        return "완성된프롬프트"

    def fake_analyze(prompt):
        calls["analyze_prompt"] = prompt
        return "RAG 답변 텍스트"

    original_build = _patch(routes, "build_rag_prompt", fake_build_rag_prompt)
    original_analyze = _patch(routes, "analyze", fake_analyze)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post(
            "/api/rag", json={"keyword": "야르", "question": "무슨 뜻이야?"}
        )
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        # trend가 반환되어야 함
        assert body["trend"] is not None, body
        # _id가 제외되어야 함
        assert "_id" not in body["trend"], f"_id should not be in trend: {body['trend']}"
        # 다른 필드는 유지되어야 함
        assert body["trend"]["keyword"] == "야르", body["trend"]
        assert body["trend"]["mention_count"] == 100, body["trend"]
    finally:
        routes.encode_batch = original_encode
        routes.search_relevant_chunks = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_rag_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] trend에 _id가 없고 다른 필드만 포함됨")


def test_sources_파라미터가_search_relevant_chunks에_그대로_전달된다():
    fake_point = SimpleNamespace(
        payload={"title": "야르 뜻 정리", "url": "https://example.com/야르", "text": "본문"}
    )
    calls = {}

    original_encode = _patch(routes, "encode_batch", lambda texts: ([[0.1, 0.2]], [{}]))

    def fake_search(keyword, dense_vec, sparse, **kwargs):
        calls["search_kwargs"] = kwargs
        return [fake_point]

    original_search = _patch(routes, "search_relevant_chunks", fake_search)
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: None)
    original_ctx = _patch(routes, "format_trend_context", lambda keyword, result=None: "")
    original_build = _patch(routes, "build_rag_prompt", lambda keyword, question, points, trend_info=None: "프롬프트")
    original_analyze = _patch(routes, "analyze", lambda prompt: "답변")
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post(
            "/api/rag",
            json={"keyword": "야르", "question": "무슨 뜻이야?", "sources": ["tavily", "youtube"]},
        )
        assert resp.status_code == 200, resp.status_code
        assert calls["search_kwargs"]["sources"] == ["tavily", "youtube"], calls
        assert calls["search_kwargs"]["is_relevant"] is True, calls
    finally:
        routes.encode_batch = original_encode
        routes.search_relevant_chunks = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_rag_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] sources 파라미터가 search_relevant_chunks로 전달됨")


def test_sources_생략하면_None으로_전달된다_기존동작():
    fake_point = SimpleNamespace(
        payload={"title": "야르 뜻 정리", "url": "https://example.com/야르", "text": "본문"}
    )
    calls = {}

    original_encode = _patch(routes, "encode_batch", lambda texts: ([[0.1, 0.2]], [{}]))

    def fake_search(keyword, dense_vec, sparse, **kwargs):
        calls["search_kwargs"] = kwargs
        return [fake_point]

    original_search = _patch(routes, "search_relevant_chunks", fake_search)
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: None)
    original_ctx = _patch(routes, "format_trend_context", lambda keyword, result=None: "")
    original_build = _patch(routes, "build_rag_prompt", lambda keyword, question, points, trend_info=None: "프롬프트")
    original_analyze = _patch(routes, "analyze", lambda prompt: "답변")
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/rag", json={"keyword": "야르", "question": "무슨 뜻이야?"})
        assert resp.status_code == 200, resp.status_code
        assert calls["search_kwargs"]["sources"] is None, calls
    finally:
        routes.encode_batch = original_encode
        routes.search_relevant_chunks = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_rag_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] sources 생략 시 None (기존 동작과 동일)")


if __name__ == "__main__":
    test_keyword나_question_누락시_400()
    test_검색결과_없으면_404()
    test_정상_흐름은_200과_답변_출처를_반환한다()
    test_trend가_있을_때_id는_제외된다()
    test_sources_파라미터가_search_relevant_chunks에_그대로_전달된다()
    test_sources_생략하면_None으로_전달된다_기존동작()
    print("\nALL PASS ✅")

"""
/api/analyze 엔드포인트 테스트. Qdrant/LLM 호출은 전부 monkeypatch로 대체.

실행: uv run python tests/test_api_analyze_endpoint.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module
import api.routes as routes


def _patch(monkeypatch_target, name, value):
    original = getattr(monkeypatch_target, name)
    setattr(monkeypatch_target, name, value)
    return original


class _FakePoint:
    def __init__(self, text, title="제목", url="https://example.com/a"):
        self.payload = {"text": text, "title": title, "url": url}


def test_keyword_없이_요청하면_400():
    app = app_module.create_app()
    client = app.test_client()
    resp = client.post("/api/analyze", json={})
    assert resp.status_code == 400, resp.status_code
    assert "error" in resp.get_json()
    print("[OK] keyword 누락 -> 400")


def test_검색_결과가_없으면_404():
    original_config = _patch(routes, "default_facet_config", lambda keyword: {})
    original_encode = _patch(routes, "encode_facets", lambda facet_config: {})
    original_search = _patch(routes, "facet_search", lambda keyword, facet_config, facet_vectors=None, is_relevant=None: ([], {}))
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/analyze", json={"keyword": "없는키워드"})
        assert resp.status_code == 404, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.default_facet_config = original_config
        routes.encode_facets = original_encode
        routes.facet_search = original_search
    print("[OK] 검색 결과 없음 -> 404")


def test_정상_흐름은_200과_결과를_반환한다():
    calls = {}
    fake_points = [_FakePoint("청크1"), _FakePoint("청크2")]

    original_config = _patch(routes, "default_facet_config", lambda keyword: {"의미": {"question": keyword}})
    original_encode = _patch(routes, "encode_facets", lambda facet_config: {"의미": {"dense": [], "sparse": {}}})

    def fake_facet_search(keyword, facet_config, facet_vectors=None, is_relevant=None):
        calls["facet_search"] = (keyword, is_relevant)
        return fake_points, {}

    original_search = _patch(routes, "facet_search", fake_facet_search)
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: {"status": "유행 중"})
    original_ctx = _patch(
        routes, "format_trend_context", lambda keyword, result=None: "트렌드요약"
    )

    def fake_build_facet_prompt(keyword, points, trend_info=None):
        calls["build_facet_prompt"] = (keyword, points, trend_info)
        return "완성된프롬프트"

    def fake_analyze(prompt):
        calls["analyze_prompt"] = prompt
        return "분석 결과 텍스트"

    original_build = _patch(routes, "build_facet_prompt", fake_build_facet_prompt)
    original_analyze = _patch(routes, "analyze", fake_analyze)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/analyze", json={"keyword": "야르"})
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["result"] == "분석 결과 텍스트", body
        assert body["trend"] == {"status": "유행 중"}, body
        assert body["sources"] == [
            {"title": "제목", "url": "https://example.com/a"},
            {"title": "제목", "url": "https://example.com/a"},
        ], body
        assert calls["facet_search"] == ("야르", True), calls
        assert calls["build_facet_prompt"] == ("야르", fake_points, "트렌드요약"), calls
        assert calls["analyze_prompt"] == "완성된프롬프트", calls
    finally:
        routes.default_facet_config = original_config
        routes.encode_facets = original_encode
        routes.facet_search = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_facet_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] 정상 흐름 200 + is_relevant=True로 facet_search 호출 + build_facet_prompt에 trend_info 전달 확인")


def test_캐시가_없으면_trend_info가_빈_문자열이다():
    calls = {}
    fake_points = [_FakePoint("청크1"), _FakePoint("청크2")]

    original_config = _patch(routes, "default_facet_config", lambda keyword: {"의미": {"question": keyword}})
    original_encode = _patch(routes, "encode_facets", lambda facet_config: {"의미": {"dense": [], "sparse": {}}})
    original_search = _patch(
        routes, "facet_search",
        lambda keyword, facet_config, facet_vectors=None, is_relevant=None: (fake_points, {}),
    )
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: None)
    original_ctx = _patch(
        routes, "format_trend_context", lambda keyword, result=None: "이건호출되면안됨"
    )

    def fake_build_facet_prompt(keyword, points, trend_info=None):
        calls["build_facet_prompt"] = (keyword, points, trend_info)
        return "완성된프롬프트"

    def fake_analyze(prompt):
        calls["analyze_prompt"] = prompt
        return "분석 결과 텍스트"

    original_build = _patch(routes, "build_facet_prompt", fake_build_facet_prompt)
    original_analyze = _patch(routes, "analyze", fake_analyze)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/analyze", json={"keyword": "야르"})
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["result"] == "분석 결과 텍스트", body
        assert body["trend"] is None, body
        assert calls["build_facet_prompt"] == ("야르", fake_points, ""), calls
        assert calls["analyze_prompt"] == "완성된프롬프트", calls
    finally:
        routes.default_facet_config = original_config
        routes.encode_facets = original_encode
        routes.facet_search = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_facet_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] 캐시 없음 -> trend_info는 빈 문자열, trend는 None")


if __name__ == "__main__":
    test_keyword_없이_요청하면_400()
    test_검색_결과가_없으면_404()
    test_정상_흐름은_200과_결과를_반환한다()
    test_캐시가_없으면_trend_info가_빈_문자열이다()
    print("\nALL PASS ✅")

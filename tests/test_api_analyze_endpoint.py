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


def test_keyword_없이_요청하면_400():
    app = app_module.create_app()
    client = app.test_client()
    resp = client.post("/api/analyze", json={})
    assert resp.status_code == 400, resp.status_code
    assert "error" in resp.get_json()
    print("[OK] keyword 누락 -> 400")


def test_청크가_없으면_404():
    original_fetch = _patch(routes, "fetch_keyword_chunks", lambda keyword: [])
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/analyze", json={"keyword": "없는키워드"})
        assert resp.status_code == 404, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.fetch_keyword_chunks = original_fetch
    print("[OK] 청크 없음 -> 404")


def test_정상_흐름은_200과_결과를_반환한다():
    calls = {}
    original_fetch = _patch(routes, "fetch_keyword_chunks", lambda keyword: ["청크1", "청크2"])
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: {"status": "유행 중"})
    original_ctx = _patch(
        routes, "format_trend_context", lambda keyword, result=None: "트렌드요약"
    )

    def fake_build_prompt(keyword, chunks, trend_info=None):
        calls["build_prompt"] = (keyword, chunks, trend_info)
        return "완성된프롬프트"

    def fake_analyze(prompt):
        calls["analyze_prompt"] = prompt
        return "분석 결과 텍스트"

    original_build = _patch(routes, "build_prompt", fake_build_prompt)
    original_analyze = _patch(routes, "analyze", fake_analyze)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/analyze", json={"keyword": "야르"})
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["result"] == "분석 결과 텍스트", body
        assert body["trend"] == {"status": "유행 중"}, body
        assert calls["build_prompt"] == ("야르", ["청크1", "청크2"], "트렌드요약"), calls
        assert calls["analyze_prompt"] == "완성된프롬프트", calls
    finally:
        routes.fetch_keyword_chunks = original_fetch
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] 정상 흐름 200 + build_prompt에 캐시된 trend_info 전달 확인")


if __name__ == "__main__":
    test_keyword_없이_요청하면_400()
    test_청크가_없으면_404()
    test_정상_흐름은_200과_결과를_반환한다()
    print("\nALL PASS ✅")

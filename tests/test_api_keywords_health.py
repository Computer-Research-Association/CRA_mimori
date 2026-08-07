"""
/api/health, /api/keywords 엔드포인트 테스트.
list_analyzable_keywords는 Mongo를 건드리므로 monkeypatch로 대체한다.

실행: uv run python tests/test_api_keywords_health.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.routes as routes
from api.app import create_app


def test_health는_ok를_반환한다():
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/health")
    assert resp.status_code == 200, resp.status_code
    assert resp.get_json() == {"status": "ok"}
    print("[OK] /api/health")


def test_keywords는_목록을_JSON으로_반환한다():
    original = routes.list_analyzable_keywords
    routes.list_analyzable_keywords = lambda: ["야르", "쌰갈"]
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/keywords")
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json() == {"keywords": ["야르", "쌰갈"]}
    finally:
        routes.list_analyzable_keywords = original
    print("[OK] /api/keywords")


if __name__ == "__main__":
    test_health는_ok를_반환한다()
    test_keywords는_목록을_JSON으로_반환한다()
    print("\nALL PASS ✅")

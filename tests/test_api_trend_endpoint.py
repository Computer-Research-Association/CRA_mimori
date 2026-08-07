"""
/api/trend/<keyword> 엔드포인트 테스트. get_cached_trend는 monkeypatch로 대체
(실시간 네이버/카카오/구글 호출이 절대 일어나지 않아야 함을 검증하는 게 이 테스트의 핵심).

실행: uv run python tests/test_api_trend_endpoint.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.routes as routes
from api.app import create_app


def test_캐시된_트렌드가_있으면_200과_함께_반환한다():
    original = routes.get_cached_trend
    routes.get_cached_trend = lambda keyword: {
        "_id": "무시되어야함",
        "keyword": keyword,
        "status": "유행 중",
        "final_z": 1.2,
    }
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/trend/야르")
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert body["status"] == "유행 중", body
        assert "_id" not in body, "Mongo _id가 응답에 그대로 노출됨"
    finally:
        routes.get_cached_trend = original
    print("[OK] 캐시된 트렌드 반환 (+_id 제거)")


def test_캐시된_트렌드가_없으면_404를_반환한다():
    original = routes.get_cached_trend
    routes.get_cached_trend = lambda keyword: None
    try:
        app = create_app()
        client = app.test_client()
        resp = client.get("/api/trend/없는키워드")
        assert resp.status_code == 404, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.get_cached_trend = original
    print("[OK] 캐시 없을 때 404")


if __name__ == "__main__":
    test_캐시된_트렌드가_있으면_200과_함께_반환한다()
    test_캐시된_트렌드가_없으면_404를_반환한다()
    print("\nALL PASS ✅")

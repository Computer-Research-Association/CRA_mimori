"""
api/app.py의 앱 팩토리 테스트.
실제 DB/Qdrant/외부 API 호출 없음 — Flask test_client와 더미 라우트로 검증.

실행: uv run python tests/test_api_app.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module


def test_없는_경로는_JSON_404를_반환한다():
    app = app_module.create_app()
    client = app.test_client()
    resp = client.get("/이런경로는없음")
    assert resp.status_code == 404, resp.status_code
    assert resp.get_json() is not None, "404 응답이 JSON이 아님"
    print("[OK] 존재하지 않는 경로 -> JSON 404")


if __name__ == "__main__":
    test_없는_경로는_JSON_404를_반환한다()
    print("\nALL PASS ✅")

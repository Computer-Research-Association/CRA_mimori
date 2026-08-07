"""
api/app.py의 앱 팩토리 + 동시성 제한 데코레이터 테스트.
실제 DB/Qdrant/외부 API 호출 없음 — Flask test_client와 더미 라우트로 검증.

실행: uv run python tests/test_api_app.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module


def test_한도_내에서는_정상_처리된다():
    app = app_module.create_app()

    @app.route("/_test_ok")
    @app_module.limited
    def _dummy():
        return {"ok": True}, 200

    client = app.test_client()
    resp = client.get("/_test_ok")
    assert resp.status_code == 200, resp.status_code
    assert resp.get_json() == {"ok": True}
    print("[OK] 세마포어 여유 있을 때 정상 처리")


def test_세마포어가_꽉_차면_429를_반환한다():
    app = app_module.create_app()

    @app.route("/_test_full")
    @app_module.limited
    def _dummy():
        return {"ok": True}, 200

    client = app.test_client()

    acquired = 0
    while app_module._semaphore.acquire(blocking=False):
        acquired += 1
    try:
        resp = client.get("/_test_full")
        assert resp.status_code == 429, resp.status_code
        assert "error" in resp.get_json()
    finally:
        for _ in range(acquired):
            app_module._semaphore.release()
    print("[OK] 세마포어 소진 시 429 반환")


def test_없는_경로는_JSON_404를_반환한다():
    app = app_module.create_app()
    client = app.test_client()
    resp = client.get("/이런경로는없음")
    assert resp.status_code == 404, resp.status_code
    assert resp.get_json() is not None, "404 응답이 JSON이 아님"
    print("[OK] 존재하지 않는 경로 -> JSON 404")


if __name__ == "__main__":
    test_한도_내에서는_정상_처리된다()
    test_세마포어가_꽉_차면_429를_반환한다()
    test_없는_경로는_JSON_404를_반환한다()
    print("\nALL PASS ✅")

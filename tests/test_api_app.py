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


def test_프록시_뒤에서도_방문자별_요청_제한이_분리된다():
    from api.rate_limit import reset
    reset()
    client = app_module.create_app().test_client()
    # 빈 요청은 DB나 작업 큐를 건드리지 않지만 IP별 제한은 적용된다.
    for _ in range(5):
        response = client.post('/api/crawl-request', json={}, headers={'X-Forwarded-For': '192.0.2.1'})
        assert response.status_code == 400
    assert client.post('/api/crawl-request', json={}, headers={'X-Forwarded-For': '192.0.2.1'}).status_code == 429
    assert client.post('/api/crawl-request', json={}, headers={'X-Forwarded-For': '192.0.2.2'}).status_code == 400
    reset()


def test_전달_체인의_마지막_프록시만_신뢰한다():
    from flask import request
    app = app_module.create_app()

    @app.route('/test-proxy')
    def proxy_details():
        return {'ip': request.remote_addr, 'scheme': request.scheme}

    response = app.test_client().get('/test-proxy', headers={
        'X-Forwarded-For': '198.51.100.1, 192.0.2.2',
        'X-Forwarded-Proto': 'https',
    })
    assert response.get_json() == {'ip': '192.0.2.2', 'scheme': 'https'}


if __name__ == "__main__":
    test_없는_경로는_JSON_404를_반환한다()
    test_프록시_뒤에서도_방문자별_요청_제한이_분리된다()
    test_전달_체인의_마지막_프록시만_신뢰한다()
    print("\nALL PASS ✅")

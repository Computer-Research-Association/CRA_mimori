"""
/api/health, /api/keywords 엔드포인트 테스트.
list_analyzable_keywords는 Mongo를 건드리므로 monkeypatch로 대체한다.

실행: uv run python tests/test_api_keywords_health.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module
import api.routes as routes


def test_health는_ok를_반환한다():
    app = app_module.create_app()
    client = app.test_client()
    resp = client.get("/api/health")
    assert resp.status_code == 200, resp.status_code
    assert resp.get_json() == {"status": "ok"}
    print("[OK] /api/health")


def test_keywords는_숨김_제외한_목록을_JSON으로_반환한다():
    original = routes.list_visible_keywords
    routes.list_visible_keywords = lambda: ["야르", "쌰갈"]
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.get("/api/keywords")
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json() == {"keywords": ["야르", "쌰갈"]}
    finally:
        routes.list_visible_keywords = original
    print("[OK] /api/keywords (숨김 제외)")


def test_hidden_keywords는_숨긴_목록을_반환한다():
    original = routes.list_hidden_keywords
    routes.list_hidden_keywords = lambda: ["오운완"]
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.get("/api/keywords/hidden")
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json() == {"keywords": ["오운완"]}
    finally:
        routes.list_hidden_keywords = original
    print("[OK] /api/keywords/hidden")


def test_존재하지_않는_키워드_숨기기는_404():
    original_list = routes.list_analyzable_keywords
    original_hide = routes.hide_keyword
    routes.list_analyzable_keywords = lambda: ["야르"]
    routes.hide_keyword = lambda keyword: (_ for _ in ()).throw(AssertionError("호출되면 안 됨"))
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/keywords/없는키워드/hide")
        assert resp.status_code == 404, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.list_analyzable_keywords = original_list
        routes.hide_keyword = original_hide
    print("[OK] 존재하지 않는 키워드 숨기기 -> 404")


def test_존재하는_키워드_숨기기는_hide_keyword를_호출한다():
    calls = []
    original_list = routes.list_analyzable_keywords
    original_hide = routes.hide_keyword
    routes.list_analyzable_keywords = lambda: ["야르"]
    routes.hide_keyword = lambda keyword: calls.append(keyword)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/keywords/야르/hide")
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json() == {"keyword": "야르", "hidden": True}
        assert calls == ["야르"], calls
    finally:
        routes.list_analyzable_keywords = original_list
        routes.hide_keyword = original_hide
    print("[OK] /api/keywords/<keyword>/hide")


def test_키워드_숨김_해제는_unhide_keyword를_호출한다():
    calls = []
    original = routes.unhide_keyword
    routes.unhide_keyword = lambda keyword: calls.append(keyword)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.post("/api/keywords/야르/unhide")
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json() == {"keyword": "야르", "hidden": False}
        assert calls == ["야르"], calls
    finally:
        routes.unhide_keyword = original
    print("[OK] /api/keywords/<keyword>/unhide")


def test_숨기지_않은_키워드_완전삭제는_400():
    original_hidden = routes.list_hidden_keywords
    original_delete = routes.delete_keyword_permanently
    routes.list_hidden_keywords = lambda: []
    routes.delete_keyword_permanently = lambda keyword: (_ for _ in ()).throw(AssertionError("호출되면 안 됨"))
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.delete("/api/keywords/야르")
        assert resp.status_code == 400, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.list_hidden_keywords = original_hidden
        routes.delete_keyword_permanently = original_delete
    print("[OK] 숨기지 않은 키워드 완전삭제 -> 400")


def test_숨긴_키워드_완전삭제는_delete_keyword_permanently를_호출한다():
    calls = []
    original_hidden = routes.list_hidden_keywords
    original_delete = routes.delete_keyword_permanently
    routes.list_hidden_keywords = lambda: ["야르"]
    routes.delete_keyword_permanently = lambda keyword: calls.append(keyword)
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.delete("/api/keywords/야르")
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json() == {"keyword": "야르", "deleted": True}
        assert calls == ["야르"], calls
    finally:
        routes.list_hidden_keywords = original_hidden
        routes.delete_keyword_permanently = original_delete
    print("[OK] DELETE /api/keywords/<keyword>")


def test_admin_stats는_get_admin_stats_결과를_그대로_반환한다():
    original = routes.get_admin_stats
    routes.get_admin_stats = lambda: {
        "keyword_count": 22, "keyword_cap": 60,
        "collection_counts": {"memes": 100}, "per_keyword_doc_counts": [],
        "capped_keywords": [],
    }
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.get("/api/admin/stats")
        assert resp.status_code == 200, resp.status_code
        assert resp.get_json()["keyword_count"] == 22
    finally:
        routes.get_admin_stats = original
    print("[OK] /api/admin/stats")


if __name__ == "__main__":
    test_health는_ok를_반환한다()
    test_keywords는_숨김_제외한_목록을_JSON으로_반환한다()
    test_hidden_keywords는_숨긴_목록을_반환한다()
    test_존재하지_않는_키워드_숨기기는_404()
    test_존재하는_키워드_숨기기는_hide_keyword를_호출한다()
    test_키워드_숨김_해제는_unhide_keyword를_호출한다()
    test_숨기지_않은_키워드_완전삭제는_400()
    test_숨긴_키워드_완전삭제는_delete_keyword_permanently를_호출한다()
    test_admin_stats는_get_admin_stats_결과를_그대로_반환한다()
    print("\nALL PASS ✅")

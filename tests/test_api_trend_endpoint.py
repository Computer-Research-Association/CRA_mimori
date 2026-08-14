"""
/api/trend/<keyword> 엔드포인트 테스트. get_cached_trend는 monkeypatch로 대체
(실시간 네이버/카카오/구글 호출이 절대 일어나지 않아야 함을 검증하는 게 이 테스트의 핵심).

실행: uv run python tests/test_api_trend_endpoint.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.app as app_module
import api.routes as routes


def test_캐시된_트렌드가_있으면_200과_함께_반환한다():
    original = routes.get_cached_trend
    routes.get_cached_trend = lambda keyword: {
        "_id": "무시되어야함",
        "keyword": keyword,
        "status": "유행 중",
        "final_z": 1.2,
    }
    try:
        app = app_module.create_app()
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
        app = app_module.create_app()
        client = app.test_client()
        resp = client.get("/api/trend/없는키워드")
        assert resp.status_code == 404, resp.status_code
        assert "error" in resp.get_json()
    finally:
        routes.get_cached_trend = original
    print("[OK] 캐시 없을 때 404")


def test_순위표는_점수_내림차순으로_정렬된다():
    original_list, original_latest = routes.list_visible_keywords, routes.get_latest_trend_for_keywords
    routes.list_visible_keywords = lambda: ["평상어", "핫한어", "감소어"]
    routes.get_latest_trend_for_keywords = lambda kws: {
        "평상어": {"status": "평상", "z_score": 0.1, "final_z": 0.1},
        "핫한어": {"status": "핫함", "z_score": 2.5, "final_z": 2.5},
        "감소어": {"status": "감소", "z_score": -1.0, "final_z": -1.0},
    }
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.get("/api/trend")
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        assert [row["keyword"] for row in body["keywords"]] == ["핫한어", "평상어", "감소어"], body
    finally:
        routes.list_visible_keywords, routes.get_latest_trend_for_keywords = original_list, original_latest
    print("[OK] 순위표는 z-score 내림차순")


def test_데이터_부족_판정도_점수와_무관하게_맨_뒤로_간다():
    """trend_scores 문서는 있지만 classify_trend가 신호 부족으로 판정한 경우.
    z_score가 0이어도(실제로 그런 값이 저장될 수 있음) 순위표 본문에 섞이면 안 된다."""
    original_list, original_latest = routes.list_visible_keywords, routes.get_latest_trend_for_keywords
    routes.list_visible_keywords = lambda: ["신호부족어", "핫한어"]
    routes.get_latest_trend_for_keywords = lambda kws: {
        "신호부족어": {"status": "데이터 부족", "z_score": 0.0, "final_z": 0.0},
        "핫한어": {"status": "핫함", "z_score": 2.5, "final_z": 2.5},
    }
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.get("/api/trend")
        body = resp.get_json()["keywords"]
        assert [row["keyword"] for row in body] == ["핫한어", "신호부족어"], body
    finally:
        routes.list_visible_keywords, routes.get_latest_trend_for_keywords = original_list, original_latest
    print("[OK] 데이터 부족 판정은 점수가 있어도 맨 뒤")


def test_트렌드_데이터가_없는_키워드는_null_상태로_맨_뒤에_온다():
    original_list, original_latest = routes.list_visible_keywords, routes.get_latest_trend_for_keywords
    routes.list_visible_keywords = lambda: ["막등록됨", "핫한어"]
    routes.get_latest_trend_for_keywords = lambda kws: {
        "핫한어": {"status": "핫함", "z_score": 2.5, "final_z": 2.5},
        # "막등록됨"은 trend_scores 문서가 아직 없는 경우를 흉내낸다 — 딕셔너리에 키 자체가 없음.
    }
    try:
        app = app_module.create_app()
        client = app.test_client()
        resp = client.get("/api/trend")
        body = resp.get_json()["keywords"]
        assert body[-1] == {"keyword": "막등록됨", "status": None, "z_score": None, "final_z": None}, body
        assert body[0]["keyword"] == "핫한어", body
    finally:
        routes.list_visible_keywords, routes.get_latest_trend_for_keywords = original_list, original_latest
    print("[OK] 데이터 없는 키워드는 null 상태로 맨 뒤")


if __name__ == "__main__":
    test_캐시된_트렌드가_있으면_200과_함께_반환한다()
    test_캐시된_트렌드가_없으면_404를_반환한다()
    test_순위표는_점수_내림차순으로_정렬된다()
    test_데이터_부족_판정도_점수와_무관하게_맨_뒤로_간다()
    test_트렌드_데이터가_없는_키워드는_null_상태로_맨_뒤에_온다()
    print("\nALL PASS ✅")

"""
routes.py
웹사이트가 호출하는 HTTP API 엔드포인트.

analyze는 더 이상 여기서 직접 계산하지 않는다 — llm_requests 컬렉션에
큐잉/조회만 하고, 실제 임베딩+검색+LLM 호출은 scheduler의
scripts/llm_request_worker.py가 처리한다(docs/superpowers/specs/2026-08-11-analyze-rag-async-perf-design.md).
"""
import functools
import hashlib
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from analysis.pipeline import (
    delete_keyword_permanently,
    hide_keyword,
    list_analyzable_keywords,
    list_hidden_keywords,
    list_visible_keywords,
    unhide_keyword,
)
from DB.mongo_client import get_collection
from config.config_cilent import ADMIN_API_KEY, CRAWL_REQUESTS_COLLECTION, LLM_REQUESTS_COLLECTION
from trend.trend_service import get_cached_trend, get_latest_trend_for_keywords
from trend.zscore import STATUS_INSUFFICIENT
from admin_stats import get_admin_stats

bp = Blueprint("api", __name__, url_prefix="/api")


def require_admin(fn):
    """관리자 전용 엔드포인트(숨김/복구/완전삭제/admin stats)에 붙인다.

    X-Admin-Key 헤더가 ADMIN_API_KEY와 일치해야 통과. ADMIN_API_KEY가
    비어있으면(미설정) fail-closed로 전부 막는다 — 설정을 깜빡한 배포가
    "인증 없음"과 같은 뜻이 되면 안 되므로.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not ADMIN_API_KEY or request.headers.get("X-Admin-Key") != ADMIN_API_KEY:
            return jsonify({"error": "관리자 인증이 필요합니다"}), 401
        return fn(*args, **kwargs)
    return wrapper


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


def _trend_rank_key(row: dict) -> tuple:
    # status가 없거나(trend_scores 문서 자체가 없음) "데이터 부족"(문서는 있지만 신호 부족
    # 판정)이면 점수가 0이든 아니든 순위를 매길 수 없는 상태다. 둘 다 맨 뒤로 보낸다.
    if row["status"] is None or row["status"] == STATUS_INSUFFICIENT:
        return (1, row["keyword"])
    score = row["final_z"] if row["final_z"] is not None else row["z_score"]
    return (0, -score) if score is not None else (1, row["keyword"])


@bp.route("/trend")
def trend_leaderboard():
    """홈 화면 순위표용 — 보이는 키워드 전체의 최신 트렌드 판정을 한 번에 반환한다.
    trend_scores가 아직 없는 키워드(막 등록된 신규 키워드 등)는 status를 null로 내려보내고,
    프론트가 이를 "데이터 부족"으로 표시한다."""
    kws = list_visible_keywords()
    latest = get_latest_trend_for_keywords(kws)
    rows = []
    for kw in kws:
        doc = latest.get(kw)
        rows.append({
            "keyword": kw,
            "status": doc.get("status") if doc else None,
            "z_score": doc.get("z_score") if doc else None,
            "final_z": doc.get("final_z") if doc else None,
        })
    rows.sort(key=_trend_rank_key)
    return jsonify({"keywords": rows})


@bp.route("/keywords")
def keywords():
    return jsonify({"keywords": list_visible_keywords()})


@bp.route("/keywords/hidden")
@require_admin
def hidden_keywords():
    return jsonify({"keywords": list_hidden_keywords()})


@bp.route("/admin/stats")
@require_admin
def admin_stats():
    return jsonify(get_admin_stats())


@bp.route("/keywords/<keyword>/hide", methods=["POST"])
@require_admin
def hide_keyword_endpoint(keyword):
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404
    hide_keyword(keyword)
    return jsonify({"keyword": keyword, "hidden": True})


@bp.route("/keywords/<keyword>/unhide", methods=["POST"])
@require_admin
def unhide_keyword_endpoint(keyword):
    unhide_keyword(keyword)
    return jsonify({"keyword": keyword, "hidden": False})


@bp.route("/keywords/<keyword>", methods=["DELETE"])
@require_admin
def delete_keyword_endpoint(keyword):
    if keyword not in list_hidden_keywords():
        return jsonify({"error": "숨긴 키워드만 완전삭제할 수 있습니다. 먼저 숨겨주세요."}), 400
    delete_keyword_permanently(keyword)
    return jsonify({"keyword": keyword, "deleted": True})


@bp.route("/trend/<keyword>")
def trend(keyword):
    result = get_cached_trend(keyword)
    if result is None:
        return jsonify({"error": f"'{keyword}'의 트렌드 데이터가 없습니다"}), 404
    result = {k: v for k, v in result.items() if k != "_id"}
    return jsonify(result)


def _analyze_job_id(keyword: str, sources: list[str] | None) -> str:
    """분석 결과 캐시 키. sources가 다르면 다른 결과가 나오므로 키에 포함시켜서,
    출처를 좁혀 재요청했을 때 예전(다른 출처) 캐시가 그대로 나오는 걸 막는다."""
    normalized_sources = ",".join(sorted(sources)) if sources else ""
    raw = f"{keyword}|{normalized_sources}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@bp.route("/analyze-request", methods=["POST"])
def analyze_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword가 필요합니다"}), 400
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404

    sources = data.get("sources")  # list[str] | None. 생략하면 전체 소스 사용
    job_id = _analyze_job_id(keyword, sources)
    collection = get_collection(LLM_REQUESTS_COLLECTION)
    existing = collection.find_one({"_id": job_id})
    if existing and existing["status"] == "failed":
        collection.update_one(
            {"_id": job_id},
            {"$set": {
                "status": "queued", "requested_at": datetime.now(timezone.utc),
                "started_at": None, "completed_at": None, "error": None, "result": None,
            }},
        )
        return jsonify({"job_id": job_id, "status": "queued"}), 202
    if existing:
        return jsonify({"job_id": job_id, "status": existing["status"]}), 202

    collection.insert_one({
        "_id": job_id, "keyword": keyword, "sources": sources,
        "status": "queued", "requested_at": datetime.now(timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    })
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@bp.route("/analyze-request/<job_id>")
def analyze_request_status(job_id):
    doc = get_collection(LLM_REQUESTS_COLLECTION).find_one({"_id": job_id})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    result = doc.get("result") or {}
    return jsonify({
        "job_id": doc["_id"],
        "keyword": doc["keyword"],
        "status": doc["status"],
        "result": result.get("result"),
        "sources": result.get("sources"),
        "trend": result.get("trend"),
        "error": doc["error"],
    })


@bp.route("/crawl-request", methods=["POST"])
def crawl_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword가 필요합니다"}), 400
    if keyword in list_analyzable_keywords():
        return jsonify({"error": "이미 존재하는 키워드입니다"}), 400

    collection = get_collection(CRAWL_REQUESTS_COLLECTION)
    existing = collection.find_one({"_id": keyword})

    if existing and existing["status"] == "failed":
        collection.update_one(
            {"_id": keyword},
            {"$set": {
                "status": "queued",
                "requested_at": datetime.now(timezone.utc),
                "started_at": None,
                "completed_at": None,
                "error": None,
                "stage": None,
                "progress": {},
            }},
        )
        return jsonify({"keyword": keyword, "status": "queued"}), 202

    if existing:
        return jsonify({"keyword": keyword, "status": existing["status"]}), 202

    collection.insert_one({
        "_id": keyword,
        "status": "queued",
        "requested_at": datetime.now(timezone.utc),
        "started_at": None,
        "completed_at": None,
        "error": None,
    })
    return jsonify({"keyword": keyword, "status": "queued"}), 202


@bp.route("/crawl-request/<keyword>")
def crawl_request_status(keyword):
    doc = get_collection(CRAWL_REQUESTS_COLLECTION).find_one({"_id": keyword})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    # stage/progress는 워커가 채우는 진행 표시용 필드다. 이 기능이 생기기 전에
    # 들어온 요청 문서에는 없으므로 get으로 읽는다(없으면 프론트가 스피너만 보여준다).
    return jsonify({
        "keyword": doc["_id"],
        "status": doc["status"],
        "requested_at": doc["requested_at"].isoformat(),
        "completed_at": doc["completed_at"].isoformat() if doc["completed_at"] else None,
        "error": doc["error"],
        "stage": doc.get("stage"),
        "progress": doc.get("progress") or {},
    })

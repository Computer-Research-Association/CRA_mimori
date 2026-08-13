"""
routes.py
웹사이트가 호출하는 HTTP API 엔드포인트.

analyze/rag는 더 이상 여기서 직접 계산하지 않는다 — llm_requests 컬렉션에
큐잉/조회만 하고, 실제 임베딩+검색+LLM 호출은 scheduler의
scripts/llm_request_worker.py가 처리한다(docs/superpowers/specs/2026-08-11-analyze-rag-async-perf-design.md).
"""
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
from config.config_cilent import CRAWL_REQUESTS_COLLECTION, LLM_REQUESTS_COLLECTION
from trend.trend_service import get_cached_trend

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@bp.route("/keywords")
def keywords():
    return jsonify({"keywords": list_visible_keywords()})


@bp.route("/keywords/hidden")
def hidden_keywords():
    return jsonify({"keywords": list_hidden_keywords()})


@bp.route("/keywords/<keyword>/hide", methods=["POST"])
def hide_keyword_endpoint(keyword):
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404
    hide_keyword(keyword)
    return jsonify({"keyword": keyword, "hidden": True})


@bp.route("/keywords/<keyword>/unhide", methods=["POST"])
def unhide_keyword_endpoint(keyword):
    unhide_keyword(keyword)
    return jsonify({"keyword": keyword, "hidden": False})


@bp.route("/keywords/<keyword>", methods=["DELETE"])
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


def _rag_job_id(keyword: str, question: str, sources: list[str] | None) -> str:
    normalized_sources = ",".join(sorted(sources)) if sources else ""
    raw = f"{keyword}|{question}|{normalized_sources}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@bp.route("/analyze-request", methods=["POST"])
def analyze_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword가 필요합니다"}), 400
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404

    collection = get_collection(LLM_REQUESTS_COLLECTION)
    existing = collection.find_one({"_id": keyword})
    if existing and existing["status"] == "failed":
        collection.update_one(
            {"_id": keyword},
            {"$set": {
                "status": "queued", "requested_at": datetime.now(timezone.utc),
                "started_at": None, "completed_at": None, "error": None, "result": None,
            }},
        )
        return jsonify({"keyword": keyword, "status": "queued"}), 202
    if existing:
        return jsonify({"keyword": keyword, "status": existing["status"]}), 202

    collection.insert_one({
        "_id": keyword, "kind": "analyze", "keyword": keyword,
        "question": None, "sources": None,
        "status": "queued", "requested_at": datetime.now(timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    })
    return jsonify({"keyword": keyword, "status": "queued"}), 202


@bp.route("/analyze-request/<keyword>")
def analyze_request_status(keyword):
    doc = get_collection(LLM_REQUESTS_COLLECTION).find_one({"_id": keyword, "kind": "analyze"})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    result = doc.get("result") or {}
    return jsonify({
        "keyword": doc["keyword"],
        "status": doc["status"],
        "result": result.get("result"),
        "sources": result.get("sources"),
        "trend": result.get("trend"),
        "error": doc["error"],
    })


@bp.route("/rag-request", methods=["POST"])
def rag_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    question = (data.get("question") or "").strip()
    if not keyword or not question:
        return jsonify({"error": "keyword와 question이 모두 필요합니다"}), 400
    if keyword not in list_analyzable_keywords():
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404

    sources = data.get("sources")  # list[str] | None
    job_id = _rag_job_id(keyword, question, sources)
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
        "_id": job_id, "kind": "rag", "keyword": keyword,
        "question": question, "sources": sources,
        "status": "queued", "requested_at": datetime.now(timezone.utc),
        "started_at": None, "completed_at": None, "error": None, "result": None,
    })
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@bp.route("/rag-request/<job_id>")
def rag_request_status(job_id):
    doc = get_collection(LLM_REQUESTS_COLLECTION).find_one({"_id": job_id, "kind": "rag"})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    result = doc.get("result") or {}
    return jsonify({
        "job_id": doc["_id"],
        "status": doc["status"],
        "answer": result.get("answer"),
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

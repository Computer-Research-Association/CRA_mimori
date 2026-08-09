"""
routes.py
웹사이트가 호출하는 HTTP API 엔드포인트. 기존 analysis/rag/trend 파이프라인
함수를 그대로 재사용하고, 여기서는 HTTP 요청/응답 형태로 감싸는 역할만 한다.
"""
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from analysis.pipeline import analyze, build_prompt, fetch_keyword_chunks, list_analyzable_keywords
from DB.mongo_client import get_collection
from config.config_cilent import CRAWL_REQUESTS_COLLECTION
from analysis.query import build_search_query
from analysis.rag_pipeline import build_rag_prompt, clean_source_url, search_relevant_chunks
from api.app import limited
from embedding.encoder import encode_batch
from trend.trend_service import format_trend_context, get_cached_trend

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@bp.route("/keywords")
def keywords():
    return jsonify({"keywords": list_analyzable_keywords()})


@bp.route("/trend/<keyword>")
def trend(keyword):
    result = get_cached_trend(keyword)
    if result is None:
        return jsonify({"error": f"'{keyword}'의 트렌드 데이터가 없습니다"}), 404
    result = {k: v for k, v in result.items() if k != "_id"}
    return jsonify(result)


@bp.route("/analyze", methods=["POST"])
@limited
def analyze_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword가 필요합니다"}), 400

    chunks = fetch_keyword_chunks(keyword)
    if not chunks:
        return jsonify({"error": f"'{keyword}' 데이터를 찾을 수 없습니다"}), 404

    cached_trend = get_cached_trend(keyword)
    if cached_trend:
        try:
            trend_info = format_trend_context(keyword, result=cached_trend)
        except Exception:
            trend_info = ""
    else:
        trend_info = ""

    prompt = build_prompt(keyword, chunks, trend_info=trend_info)
    result = analyze(prompt)

    trend_response = {k: v for k, v in cached_trend.items() if k != "_id"} if cached_trend else None
    return jsonify({"result": result, "trend": trend_response})


@bp.route("/rag", methods=["POST"])
@limited
def rag_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    question = (data.get("question") or "").strip()
    if not keyword or not question:
        return jsonify({"error": "keyword와 question이 모두 필요합니다"}), 400

    search_query = build_search_query(keyword, question)
    dense_vecs, lexical_weights = encode_batch([search_query])
    points = search_relevant_chunks(keyword, dense_vecs[0], lexical_weights[0], is_relevant=True)
    if not points:
        return jsonify({"error": f"'{keyword}'에 대한 검색 결과가 없습니다"}), 404

    cached_trend = get_cached_trend(keyword)
    if cached_trend:
        try:
            trend_info = format_trend_context(keyword, result=cached_trend)
        except Exception:
            trend_info = ""
    else:
        trend_info = ""

    prompt = build_rag_prompt(keyword, question, points, trend_info=trend_info)
    answer = analyze(prompt)

    sources = [
        {
            "title": point.payload.get("title") or "제목 없음",
            "url": clean_source_url(point.payload.get("url")),
        }
        for point in points
    ]
    trend_response = {k: v for k, v in cached_trend.items() if k != "_id"} if cached_trend else None
    return jsonify({"answer": answer, "sources": sources, "trend": trend_response})


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
    return jsonify({
        "keyword": doc["_id"],
        "status": doc["status"],
        "requested_at": doc["requested_at"].isoformat(),
        "completed_at": doc["completed_at"].isoformat() if doc["completed_at"] else None,
        "error": doc["error"],
    })

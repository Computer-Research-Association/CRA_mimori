"""
routes.py
웹사이트가 호출하는 HTTP API 엔드포인트. 기존 analysis/rag/trend 파이프라인
함수를 그대로 재사용하고, 여기서는 HTTP 요청/응답 형태로 감싸는 역할만 한다.
"""
from flask import Blueprint, jsonify, request

from analysis.pipeline import analyze, build_prompt, fetch_keyword_chunks, list_analyzable_keywords
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
    trend_info = format_trend_context(keyword, result=cached_trend) if cached_trend else ""

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
    trend_info = format_trend_context(keyword, result=cached_trend) if cached_trend else ""

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

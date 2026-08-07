"""
routes.py
웹사이트가 호출하는 HTTP API 엔드포인트. 기존 analysis/rag/trend 파이프라인
함수를 그대로 재사용하고, 여기서는 HTTP 요청/응답 형태로 감싸는 역할만 한다.
"""
from flask import Blueprint, jsonify, request

from analysis.pipeline import analyze, build_prompt, fetch_keyword_chunks, list_analyzable_keywords
from api.app import limited
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

    return jsonify({"result": result, "trend": cached_trend})

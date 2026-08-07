"""
routes.py
웹사이트가 호출하는 HTTP API 엔드포인트. 기존 analysis/rag/trend 파이프라인
함수를 그대로 재사용하고, 여기서는 HTTP 요청/응답 형태로 감싸는 역할만 한다.
"""
from flask import Blueprint, jsonify

from analysis.pipeline import list_analyzable_keywords

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@bp.route("/keywords")
def keywords():
    return jsonify({"keywords": list_analyzable_keywords()})

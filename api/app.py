"""
app.py
Flask 앱 팩토리.
"""
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException


def create_app() -> Flask:
    app = Flask(__name__)
    # frontend/vite.config.js(로컬 dev)와 frontend/nginx.conf(배포) 둘 다 /api를
    # 같은 오리진으로 프록시하므로, 이 앱 자체를 쓰는 정상 경로는 브라우저 cross-origin
    # 요청이 전혀 필요 없다. docker-compose가 5000 포트를 직접 노출하고 있어(admin
    # 라우트 게이트가 그 노출의 완화책), CORS를 전체 허용으로 두면 제3자 페이지가
    # 방문자 브라우저를 시켜 /crawl-request 등을 직접 두드려 그 방문자의 IP 기준
    # rate-limit·쿼터를 갉아먹을 수 있다. 실제 프론트 도메인으로 좁힌다.
    CORS(app, origins=["https://mimori-cra.duckdns.org"])

    from api.routes import bp

    app.register_blueprint(bp)

    @app.errorhandler(Exception)
    def handle_error(exc):
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description}), exc.code
        app.logger.exception("처리되지 않은 예외")
        return jsonify({"error": "서버 내부 오류"}), 500

    return app


app = create_app()

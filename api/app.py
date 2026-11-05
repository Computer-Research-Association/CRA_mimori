"""
app.py
Flask 앱 팩토리.
"""
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix


def create_app() -> Flask:
    app = Flask(__name__)
    # 운영 API는 localhost에만 공개되고 nginx 한 단계를 거친다.
    # nginx가 전달 헤더를 덮어쓰므로 방문자가 보낸 IP를 그대로 신뢰하지 않는다.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    CORS(app, origins=[
        "https://mimori-cra.duckdns.org",
        "https://mememory-cra.duckdns.org",
    ])

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

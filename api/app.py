"""
app.py
Flask 앱 팩토리.
"""
from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)  # 프론트 도메인이 아직 없어 전체 허용. 나중에 도메인이 정해지면 좁힌다.

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

"""
app.py
Flask 앱 팩토리 + 동시 처리 상한 데코레이터.

동시성 제한 이유: EC2가 GPU 없는 버스터블 CPU 인스턴스이고, /api/analyze와 /api/rag는
요청마다 BGE-M3 임베딩(RAG만) + LLM 호출을 돌린다. 여러 요청이 동시에 몰리면 같은
인스턴스에 떠 있는 mongo/qdrant/크롤러 스케줄러까지 같이 느려질 수 있어, 자체 인프라
보호 목적으로 동시 처리 개수를 코드로 제한한다(사용자별 rate limit과는 목적이 다름).
"""
import threading
from functools import wraps

from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

MAX_CONCURRENT_REQUESTS = 2
_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)


def limited(view_func):
    """MAX_CONCURRENT_REQUESTS를 넘는 동시 요청은 즉시 429로 거절한다(대기 큐 없음)."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not _semaphore.acquire(blocking=False):
            return jsonify({"error": "서버가 바쁩니다. 잠시 후 다시 시도하세요."}), 429
        try:
            return view_func(*args, **kwargs)
        finally:
            _semaphore.release()

    return wrapper


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

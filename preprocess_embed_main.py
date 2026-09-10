"""
preprocess_embed_main.py
스케줄러가 매일 자동으로 호출하는 비대화형(non-interactive) 배치 진입점.
memes.is_embedded=False인 문서 전체를 대상으로 전처리(청킹) → 임베딩(Qdrant 적재) 순서로 처리한다.

preprocess_main.py / embed_main.py와 하는 일은 같지만, input()으로 키워드를 묻지 않고
항상 "전체 미처리 문서"를 대상으로 한 번에 처리한다 — 사람 개입 없이 크론/스케줄러에서
호출하기 위함.
"""

import sys
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from preprocessing.pipeline import preprocess_documents
from embedding.pipeline import embed_documents
from scripts.heavy_job_lock import acquire_heavy_job_lock_blocking, release_heavy_job_lock

# scheduler.py가 쓰는 것과 같은 상태 로그 파일. scheduler.py를 직접 import하면
# 그 모듈 최상단에서 스케줄러가 실제로 기동돼버리므로(부작용 있음), 여기서는
# 같은 경로에 쓰는 최소 로깅 함수를 별도로 둔다 — 수동 실행/자동 실행 어느
# 쪽이든 이 파일 하나만 보고 중간 진행 상황을 판단할 수 있게 하기 위함.
STATUS_PATH = os.path.join(BASE_DIR, "scheduler", "status.txt")


def log_status(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATUS_PATH, "a", encoding="utf-8") as f:
        f.write(f"{now} - [preprocess_embed] {message}\n")


if __name__ == "__main__":
    log_status("전처리 시작 (전체 미처리 문서)")
    print("[preprocess_embed] 전처리 시작 (전체 미처리 문서)")
    chunks = preprocess_documents(None)
    log_status(f"전처리 완료: 청크 {len(chunks)}개 생성")
    print(f"[preprocess_embed] 전처리 완료: 청크 {len(chunks)}개 생성")

    # BGE-M3를 메모리에 올리는 구간이므로, crawl_request_worker/llm_request_worker와
    # 동시에 로드되지 않도록 heavy_job_lock으로 감싼다. 배치는 사람이 기다리지
    # 않으므로 락을 못 잡으면 포기하고 다음 실행(스케줄러 catch-up)에 맡긴다 —
    # is_embedded=False로 남은 문서는 다음 실행에서 그대로 재처리된다.
    if not acquire_heavy_job_lock_blocking("preprocess_embed_main"):
        log_status("임베딩 건너뜀: 락 획득 시간 초과(다른 무거운 작업이 오래 실행 중)")
        print("[preprocess_embed] 임베딩 건너뜀: 락 획득 시간 초과(다른 무거운 작업이 오래 실행 중)")
        sys.exit(0)

    log_status("임베딩 시작 (전체 미처리 문서)")
    print("[preprocess_embed] 임베딩 시작 (전체 미처리 문서)")
    try:
        result = embed_documents(None)
    finally:
        release_heavy_job_lock("preprocess_embed_main")
    log_status(
        f"임베딩 완료: 문서 {result['documents']}개 "
        f"/ 청크 {result['chunks']}개 / 실패 {result.get('failed', 0)}개"
    )
    print(
        f"[preprocess_embed] 임베딩 완료: 문서 {result['documents']}개 "
        f"/ 청크 {result['chunks']}개 / 실패 {result.get('failed', 0)}개"
    )

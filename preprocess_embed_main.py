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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from preprocessing.pipeline import preprocess_documents
from embedding.pipeline import embed_documents

if __name__ == "__main__":
    print("[preprocess_embed] 전처리 시작 (전체 미처리 문서)")
    chunks = preprocess_documents(None)
    print(f"[preprocess_embed] 전처리 완료: 청크 {len(chunks)}개 생성")

    print("[preprocess_embed] 임베딩 시작 (전체 미처리 문서)")
    result = embed_documents(None)
    print(
        f"[preprocess_embed] 임베딩 완료: 문서 {result['documents']}개 "
        f"/ 청크 {result['chunks']}개 / 실패 {result.get('failed', 0)}개"
    )

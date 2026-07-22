"""
rag_main.py
밈/신조어 키워드를 고르고 자유 텍스트로 질문하면, Qdrant 하이브리드 검색으로
관련 청크를 찾아 그걸 근거로 로컬 LLM(Ollama)이 답변한다.

main.py / analyze_main.py와 동일하게 루트에서 바로 실행 가능:
    python rag_main.py
"""

import sys
import os

# torch와 다른 네이티브 의존성(qdrant-client/ollama 등)이 각자 별도의 Intel
# OpenMP 런타임(libiomp5md.dll)을 들고 있어, 한 프로세스에서 같이 로드되면
# Windows에서 세그폴트가 난다. import torch가 일어나기 전에 반드시 설정해야 함.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from analysis.pipeline import analyze, list_analyzable_keywords
from analysis.rag_pipeline import build_rag_prompt, search_relevant_chunks
from embedding.encoder import encode_batch, unload_model

if __name__ == "__main__":
    keywords = list_analyzable_keywords()
    if not keywords:
        print("분석할 수 있는 키워드가 없습니다.")
        sys.exit(1)

    print("검색 가능한 키워드:")
    for i, keyword in enumerate(keywords, start=1):
        print(f"  {i}. {keyword}")

    raw_choice = input("키워드 선택 (번호 입력): ").strip()
    if not raw_choice.isdigit() or not (1 <= int(raw_choice) <= len(keywords)):
        print("잘못된 선택입니다.")
        sys.exit(1)

    selected_keyword = keywords[int(raw_choice) - 1]

    question = input("질문을 입력하세요: ").strip()
    if not question:
        print("질문을 입력하세요.")
        sys.exit(1)

    print("[검색 중] 관련 청크 조회...")
    dense_vecs, lexical_weights = encode_batch([question])
    unload_model()  # Ollama가 GPU를 쓸 수 있게 임베딩 모델을 미리 내려둠 (VRAM 충돌 방지)
    points = search_relevant_chunks(selected_keyword, dense_vecs[0], lexical_weights[0])
    if not points:
        print("검색 결과가 없습니다.")
        sys.exit(1)
    print(f"[검색 완료] 관련 청크 {len(points)}개 발견")

    prompt = build_rag_prompt(selected_keyword, question, points)

    print("[답변 생성 중] LLM에게 질의 중...")
    answer = analyze(prompt)

    print()
    print("=" * 40)
    print(f"질문: {question}")
    print("=" * 40)
    print(answer)
    print()
    print("-- 근거 출처 --")
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = point.payload.get("url") or "출처 없음"
        print(f"  - {title} ({url})")

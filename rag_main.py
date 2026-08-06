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
from analysis.query import build_search_query
from analysis.rag_pipeline import (
    build_facet_prompt,
    build_rag_prompt,
    clean_source_url,
    default_facet_config,
    encode_facets,
    facet_search,
    search_relevant_chunks,
)
from embedding.encoder import encode_batch, unload_model
from trend.trend_service import format_trend_context

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

    # 자유질문(기본)과 facet 4항목 분석 중 선택. facet 모드는 의미/유행_이유/사용법/사용자층
    # 네 각도로 나눠 검색·병합하는 langchain_playground.ipynb 흐름을 그대로 쓴다.
    mode = input("분석 모드 [1] 자유질문(기본)  [2] facet 4항목 분석: ").strip() or "1"

    # 유행 판정(z-score 앙상블)을 한 번만 계산해 콘솔에 보여주고, 아래 프롬프트 빌더에
    # 그대로 넘겨 중복 네트워크 호출(네이버/카카오/구글)을 막는다. ""이면 데이터 부족/수집 실패.
    trend_info = format_trend_context(selected_keyword)
    print("[트렌드 판정]")
    print(trend_info if trend_info else "  판정 불가 (데이터 부족 또는 수집 실패)")

    if mode == "2":
        print("[검색 중] facet 4각도 검색...")
        facet_config = default_facet_config(selected_keyword)
        # 임베딩을 먼저 끝내고 unload_model()로 VRAM을 비운 뒤 검색한다(자유질문 경로와 동일한
        # 순서). facet_vectors를 넘겨 facet_search 내부에서 재임베딩하지 않게 한다.
        facet_vectors = encode_facets(facet_config)
        unload_model()  # LLM이 GPU를 쓸 수 있게 임베딩 모델을 미리 내려둠 (VRAM 충돌 방지)
        points, diag = facet_search(
            selected_keyword, facet_config, facet_vectors=facet_vectors, is_relevant=True
        )
        if not points:
            print("검색 결과가 없습니다.")
            sys.exit(1)
        print(f"[검색 완료] 병합 컨텍스트 {len(points)}개 (소스분포={diag['source_counts']})")
        prompt = build_facet_prompt(selected_keyword, points, trend_info=trend_info)
        question_label = "facet 4항목 분석 (의미/유행 이유/사용법/사용자층)"
    else:
        question = input("질문을 입력하세요: ").strip()
        if not question:
            print("질문을 입력하세요.")
            sys.exit(1)

        print("[검색 중] 관련 청크 조회...")
        # 검색(임베딩)에 넣는 쿼리에는 keyword를 앞에 붙인다 — eval 경로와 동일한 조립.
        # 단, 아래 build_rag_prompt에는 원본 question을 그대로 넘겨 LLM이 유저가 실제로
        # 물은 질문을 보게 한다.
        search_query = build_search_query(selected_keyword, question)
        dense_vecs, lexical_weights = encode_batch([search_query])
        unload_model()  # Ollama가 GPU를 쓸 수 있게 임베딩 모델을 미리 내려둠 (VRAM 충돌 방지)
        points = search_relevant_chunks(selected_keyword, dense_vecs[0], lexical_weights[0], is_relevant=True)
        if not points:
            print("검색 결과가 없습니다.")
            sys.exit(1)
        print(f"[검색 완료] 관련 청크 {len(points)}개 발견")

        prompt = build_rag_prompt(selected_keyword, question, points, trend_info=trend_info)
        question_label = question

    print("[답변 생성 중] LLM에게 질의 중...")
    answer = analyze(prompt)

    print()
    print("=" * 40)
    print(f"질문: {question_label}")
    print("=" * 40)
    print(answer)
    print()
    print("-- 근거 출처 --")
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = clean_source_url(point.payload.get("url"))
        print(f"  - {title} ({url})")

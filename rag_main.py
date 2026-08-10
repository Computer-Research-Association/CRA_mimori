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
from trend.trend_service import format_trend_for_rag

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

    # 유행 판정은 키워드당 한 번만 계산 (네트워크 중복 호출 방지)
    print(f"[트렌드 조회 중] '{selected_keyword}' 유행 판정 중... (10~30초 소요)")
    trend_console, trend_info = format_trend_for_rag(selected_keyword)
    print("[트렌드 조회 완료]")

    print(f"\n'{selected_keyword}' 세션 시작. 종료하려면 'exit' 입력.\n")

    while True:
        mode = input("[1] 자유질문  [2] facet 4항목 분석  [exit] 종료 > ").strip()

        if mode.lower() == "exit":
            print("종료합니다.")
            break

        if mode == "2":
            print("[검색 중] facet 4각도 검색...")
            facet_config = default_facet_config(selected_keyword)
            facet_vectors = encode_facets(facet_config)
            unload_model()
            points, diag = facet_search(
                selected_keyword, facet_config, facet_vectors=facet_vectors, is_relevant=True
            )
            if not points:
                print("검색 결과가 없습니다.\n")
                continue
            print(f"[검색 완료] 병합 컨텍스트 {len(points)}개 (소스분포={diag['source_counts']})")
            prompt = build_facet_prompt(selected_keyword, points, trend_info=trend_info)
            question_label = "분석."
        else:
            question = input("질문을 입력하세요 (exit: 종료) > ").strip()
            if question.lower() == "exit":
                print("종료합니다.")
                break
            if not question:
                continue

            print("[검색 중] 관련 청크 조회...")
            search_query = build_search_query(selected_keyword, question)
            dense_vecs, lexical_weights = encode_batch([search_query])
            unload_model()
            points = search_relevant_chunks(selected_keyword, dense_vecs[0], lexical_weights[0], is_relevant=True)
            if not points:
                print("검색 결과가 없습니다.\n")
                continue
            print(f"[검색 완료] 관련 청크 {len(points)}개 발견")

            prompt = build_rag_prompt(selected_keyword, question, points, trend_info=trend_info)
            question_label = f"질문: {question}"

        print("[답변 생성 중] LLM에게 질의 중...")
        answer = analyze(prompt)

        print()
        print("=" * 40)
        print("[트렌드 판정]")
        print("z-score 기준: z>2=핫함 / z≥0.5=유행 중 / |z|<0.5=평상 / z≥-2=감소 / z<-2=소멸")
        print(trend_console)
        print("=" * 40)
        print(question_label)
        print("=" * 40)
        print(answer)
        print()
        print("-- 근거 출처 --")
        for point in points:
            title = point.payload.get("title") or "제목 없음"
            url = clean_source_url(point.payload.get("url"))
            print(f"  - {title} ({url})")
        print()

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

import main
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
from embedding.pipeline import embed_documents
from preprocessing.pipeline import preprocess_documents
from trend.trend_service import format_trend_for_rag


def ensure_keyword_ready(keyword: str) -> bool:
    """Qdrant에 이 키워드의 임베딩이 없으면(=list_analyzable_keywords에 없으면) 그
    자리에서 크롤 → 전처리 → 임베딩까지 수행한다(issue #69, 온디맨드 RAG 크롤).

    main.py의 배치 크롤(Keywords.md 전체를 매일 무인 처리)과 별개로, 사용자가
    질문한 키워드 하나만 즉시 수집한다. crawl_keyword()는 main.py의 크롤
    우선순위(커뮤니티 우선 → 부족 시 Tavily/DuckDuckGo 보완)를 그대로 재사용한다.

    반환: 이후 검색이 가능한 상태가 됐으면 True, 수집된 문서가 0건이라 답변할
    근거가 없으면 False.
    """
    print(f"[온디맨드] '{keyword}'는 아직 수집된 적 없는 키워드입니다. 지금 크롤링합니다... (수십 초 소요될 수 있음)")
    results = main.crawl_keyword(keyword)
    total = sum(count for count, _ in results.values())
    print(f"[온디맨드] 크롤 완료 — 총 {total}건 수집")

    if total == 0:
        print(f"[온디맨드] 수집된 문서가 없어 '{keyword}'에 대해 답변할 수 없습니다.")
        return False

    print("[온디맨드] 전처리(정제+청킹) 중...")
    preprocess_documents(keyword)

    print("[온디맨드] 임베딩 중...")
    embed_documents(keyword)

    if main.add_keyword_if_missing(keyword):
        print(f"[온디맨드] '{keyword}'를 Keywords.md에 추가했습니다 (다음 배치부터 자동으로 트렌드 추적 대상).")

    return True


if __name__ == "__main__":
    keywords = list_analyzable_keywords()
    if not keywords:
        print("분석할 수 있는 키워드가 없습니다.")
        sys.exit(1)

    print("검색 가능한 키워드:")
    for i, keyword in enumerate(keywords, start=1):
        print(f"  {i}. {keyword}")

    raw_choice = input("키워드 선택 (번호 입력, 또는 목록에 없는 새 키워드 직접 입력): ").strip()
    if not raw_choice:
        print("잘못된 선택입니다.")
        sys.exit(1)

    if raw_choice.isdigit() and 1 <= int(raw_choice) <= len(keywords):
        selected_keyword = keywords[int(raw_choice) - 1]
    else:
        # 번호가 아니거나 목록 범위를 벗어난 입력은 새 키워드로 간주한다.
        selected_keyword = raw_choice
        if selected_keyword not in keywords and not ensure_keyword_ready(selected_keyword):
            sys.exit(1)

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

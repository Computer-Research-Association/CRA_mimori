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
from config.config_cilent import MIN_COMMUNITY_DOCS_FOR_TAVILY
from embedding.encoder import encode_batch, unload_model
from trend.trend_service import format_trend_for_rag


def ensure_keyword_ready(keyword: str) -> bool:
    """Qdrant에 이 키워드의 임베딩이 없으면(=list_analyzable_keywords에 없으면) 그
    자리에서 크롤 → 전처리 → 임베딩까지 수행한다(issue #69, 온디맨드 RAG 크롤).

    main.py의 배치 크롤(Keywords.md 전체를 매일 무인 처리)과 별개로, 사용자가
    질문한 키워드 하나만 즉시 수집한다. 수집 우선순위(커뮤니티 우선 → 부족 시
    Tavily/DuckDuckGo 보완)는 crawl_keyword()가 crawl_all()에 위임하므로 배치와 같다.

    반환: 이후 검색이 가능한 상태가 됐으면 True, 근거로 쓸 청크가 하나도 안 남아
    답변이 불가능하면 False.
    """
    # 온디맨드는 드물게만 타는 경로인데 main 모듈은 크롤러 전체와 boto3(CloudWatch
    # 핸들러)를 끌고 와서 import 만으로 몇 초가 든다. 이미 임베딩된 키워드로 질문하는
    # 대다수 실행이 그 비용을 물지 않도록 여기서 지연 import 한다.
    import main
    from embedding.pipeline import embed_documents
    from preprocessing.pipeline import preprocess_documents

    print(f"[온디맨드] '{keyword}'는 아직 수집된 적 없는 키워드입니다. 지금 크롤링합니다.")
    print("[온디맨드] 커뮤니티 소스를 병렬로 돕니다 — 사이트 응답에 따라 수 분에서 20분 넘게 걸릴 수 있습니다.")

    def report(_keyword: str, source: str, count: int, status: str) -> None:
        cell = f"{count}건" if status == "ok" else f"{count}건 [{status}]"
        print(f"  [온디맨드] {source:<12}: {cell}")

    results = main.crawl_keyword(keyword, on_source_done=report)
    total = sum(count for count, _ in results.values())
    print(f"[온디맨드] 크롤 완료 — 총 {total}건 수집")

    if total == 0:
        print(f"[온디맨드] 수집된 문서가 없어 '{keyword}'에 대해 답변할 수 없습니다.")
        return False

    print("[온디맨드] 전처리(정제+청킹) 중...")
    preprocess_documents(keyword)

    print("[온디맨드] 임베딩 중...")
    stats = embed_documents(keyword)

    # 크롤이 0건이 아니어도 검색 가능한 청크가 0개로 끝날 수 있다 — 전처리의 관련성
    # 판정(judge_doc)이 전부 걸러내거나, 임베딩 단계의 근접중복 스킵에 다 걸리는 경우.
    # 여기서 True를 돌려주면 사용자는 몇 분 기다린 뒤 "검색 결과가 없습니다"만 반복해서
    # 보고 이유는 알 수 없으므로, 사유를 밝히고 끊는다.
    if stats.get("chunks", 0) == 0:
        print(f"[온디맨드] {total}건을 수집했지만 '{keyword}'와 관련 있는 내용이 남지 않아 "
              "(관련성 필터/중복 제거) 답변 근거가 없습니다.")
        return False

    print(f"[온디맨드] 임베딩 완료 — 문서 {stats.get('documents', 0)}건 / 청크 {stats['chunks']}개")

    # Keywords.md에 넣는 순간 매일 배치 크롤·트렌드 판정 대상이 되고 빼는 경로는 없다.
    # 오타나 일회성 질의가 영구히 Tavily/YouTube 쿼터를 갉아먹지 않도록, 배치에서 커뮤니티
    # 수집이 '충분하다'고 보는 기준(MIN_COMMUNITY_DOCS_FOR_TAVILY)을 넘긴 키워드만 편입한다.
    if stats.get("documents", 0) < MIN_COMMUNITY_DOCS_FOR_TAVILY:
        print(f"[온디맨드] 수집량이 적어(문서 {stats.get('documents', 0)}건) Keywords.md에는 추가하지 않습니다. "
              "이번 질의에는 그대로 사용합니다.")
    elif main.add_keyword_if_missing(keyword):
        print(f"[온디맨드] '{keyword}'를 Keywords.md에 추가했습니다 (다음 배치부터 자동으로 트렌드 추적 대상).")

    return True


def select_keyword(keywords: list[str], ask=input) -> str | None:
    """키워드 목록을 보여주고 사용자의 선택을 키워드 문자열로 돌려준다.
    입력이 비었으면(=선택 취소) None.

    keywords가 비어 있어도 종료하지 않는다 — 임베딩된 키워드가 하나도 없는 상태
    (새 배포/DB 초기화 직후)가 온디맨드 수집이 가장 필요한 순간인데, 예전처럼
    여기서 끊으면 기능 자체를 쓸 수 없다(issue #69).
    """
    if not keywords:
        print("아직 임베딩된 키워드가 없습니다. 검색할 키워드를 입력하면 지금 수집합니다.")
        return ask("키워드 입력: ").strip() or None

    print("검색 가능한 키워드:")
    for i, keyword in enumerate(keywords, start=1):
        print(f"  {i}. {keyword}")
    print("  0. (목록에 없는 키워드 직접 입력)")

    raw_choice = ask("키워드 선택 (번호, 또는 키워드 직접 입력): ").strip()
    if not raw_choice:
        return None

    # 숫자로만 된 신조어(예: "1빠"가 아니라 "1")를 목록 번호와 구분해 입력받는 통로.
    if raw_choice == "0":
        return ask("새 키워드 입력: ").strip() or None

    if raw_choice.isdigit() and 1 <= int(raw_choice) <= len(keywords):
        return keywords[int(raw_choice) - 1]

    # 번호가 아니거나 목록 범위를 벗어난 입력은 새 키워드로 간주한다.
    return raw_choice


if __name__ == "__main__":
    keywords = list_analyzable_keywords()

    selected_keyword = select_keyword(keywords)
    if selected_keyword is None:
        print("잘못된 선택입니다.")
        sys.exit(1)

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

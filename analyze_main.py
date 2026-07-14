"""
analyze_main.py
이미 임베딩된 밈 키워드 중 하나를 선택하면, Qdrant에 저장된 해당 키워드의
전체 청크를 로컬 LLM(Ollama)에게 보여주고 분석 결과를 받아 출력한다.

기초적인 분석 ㅍ개선 여지 엄청 많음

main.py / preprocess_main.py / embed_main.py와 동일하게 루트에서 바로 실행 가능:
    python analyze_main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from analysis.pipeline import (
    analyze,
    build_prompt,
    fetch_keyword_chunks,
    list_analyzable_keywords,
)

if __name__ == "__main__":
    keywords = list_analyzable_keywords()
    if not keywords:
        print("분석할 수 있는 키워드가 없습니다.")
        sys.exit(1)

    print("분석 가능한 키워드:")
    for i, keyword in enumerate(keywords, start=1):
        print(f"  {i}. {keyword}")

    raw_choice = input("키워드 선택 (번호 입력): ").strip()
    if not raw_choice.isdigit() or not (1 <= int(raw_choice) <= len(keywords)):
        print("잘못된 선택입니다.")
        sys.exit(1)

    selected_keyword = keywords[int(raw_choice) - 1]

    print(f"[조회 중] '{selected_keyword}' 관련 청크 수집...")
    chunks = fetch_keyword_chunks(selected_keyword)
    if not chunks:
        print("Qdrant에서 청크를 찾을 수 없습니다.")
        sys.exit(1)
    print(f"[조회 완료] 청크 {len(chunks)}개 수집됨")

    prompt = build_prompt(selected_keyword, chunks)

    print("[분석 중] LLM에게 질의 중...")
    result = analyze(prompt)

    print()
    print("=" * 40)
    print(f"'{selected_keyword}' 분석 결과")
    print("=" * 40)
    print(result)

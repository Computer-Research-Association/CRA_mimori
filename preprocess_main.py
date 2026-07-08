"""
preprocess_main.py
MongoDB(memes)의 미처리 문서를 전처리(cleaner) + 청킹(chunker) 처리해
cleaned_memes 컬렉션에 저장.

main.py와 동일하게 루트에서 바로 실행 가능:
    python preprocess_main.py

키워드를 입력하면 그 키워드 문서만, 그냥 Enter를 누르면 전체 문서를 처리한다.
실행할 때마다 방금 처리한 문서 중 일부를 터미널에 전/후 비교로 바로 보여준다.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.config_cilent import CLEANED_COLLECTION
from DB.mongo_client import get_collection
from preprocessing.pipeline import preprocess_documents
from preprocessing.parent_lookup import get_parent


def _print_sample_preview(keyword: str | None, limit: int = 2):
    """방금 처리된 결과 중 일부를 전/후 비교 + parent_id 매핑 검증으로 출력."""
    collection = get_collection(CLEANED_COLLECTION)
    query = {"keyword": keyword} if keyword else {}
    samples = list(collection.find(query).sort("processed_at", -1).limit(limit))

    for doc in samples:
        print("-" * 60)
        print(f"[{doc['source']}] {doc['title']}")
        print(f"청크 수: {doc['chunk_count']}")
        print()
        print("[BEFORE] 원본 (앞 200자)")
        print(doc["content"][:200])
        print()
        print("[AFTER] 정제 (앞 200자)")
        print(doc["clean_content"][:200])
        print()

        chunks = doc.get("chunks", [])
        if not chunks:
            continue

        first_chunk = chunks[0]
        print(f"[청크 예시 0/{doc['chunk_count']}] (section={first_chunk['section_title']})")
        print(first_chunk["text"][:200])
        print()

        # child → parent 매핑 검증
        parent_id = first_chunk.get("parent_id")
        parent_doc = get_parent(parent_id)
        if parent_doc is None:
            print(f"[검증 실패] parent_id={parent_id} → memes 컬렉션에서 찾을 수 없음")
        else:
            chunk_text = first_chunk["text"]
            # clean_content 기준으로 확인 (전처리 후 텍스트이므로)
            haystack = parent_doc.get("content", "")
            # 청크 텍스트 앞 30자를 원본에서 검색 (overlap 때문에 완전 일치 대신 부분 검색)
            needle = chunk_text[:30]
            if needle in haystack:
                print(f"[검증 OK] parent_id={parent_id} → 원본 문서에서 청크 텍스트 확인됨")
            else:
                print(f"[검증 주의] parent_id={parent_id} → 원본에서 앞 30자 미발견 (정제 과정에서 변형됐을 수 있음)")
            print(f"           원본 제목: {parent_doc.get('title', '(없음)')}")

    print("-" * 60)


if __name__ == "__main__":
    keyword_input = input("처리할 키워드 입력 (전체 처리하려면 그냥 Enter): ").strip()
    target_keyword = keyword_input or None

    chunks = preprocess_documents(target_keyword)

    print()
    print("=" * 40)
    label = f"키워드: '{target_keyword}'" if target_keyword else "전체 문서"
    print(f"전처리 + 청킹 완료 ({label})")
    print("=" * 40)
    print(f"  생성된 청크 총합: {len(chunks)}개")
    print(f"  저장 위치       : '{CLEANED_COLLECTION}' 컬렉션")
    print()

    if chunks:
        print("샘플 미리보기 (전/후 비교 + 매핑 검증):")
        _print_sample_preview(target_keyword)
    else:
        print("처리할 문서가 없습니다.")


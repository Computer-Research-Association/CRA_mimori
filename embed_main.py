"""
embed_main.py
cleaned_memes의 청크를 BGE-M3(dense+sparse)로 임베딩해 Qdrant(mimori_chunks)에 적재.

main.py / preprocess_main.py와 동일하게 루트에서 바로 실행 가능:
    python embed_main.py

키워드를 입력하면 그 키워드 문서만, 그냥 Enter를 누르면 memes.is_embedded=False인
문서 전체를 처리한다.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from embedding.pipeline import embed_documents

if __name__ == "__main__":
    keyword_input = input("임베딩할 키워드 입력 (전체 처리하려면 그냥 Enter): ").strip()
    target_keyword = keyword_input or None

    result = embed_documents(target_keyword)

    print()
    print("=" * 40)
    label = f"키워드: '{target_keyword}'" if target_keyword else "전체 문서"
    print(f"임베딩 완료 ({label})")
    print("=" * 40)
    print(f"  처리된 문서: {result['documents']}개")
    print(f"  적재된 청크: {result['chunks']}개")
    print(f"  실패한 문서: {result.get('failed', 0)}개")

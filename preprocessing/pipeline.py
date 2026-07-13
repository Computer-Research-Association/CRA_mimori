"""
pipeline.py
MongoDB에 저장된 원문 문서를 정제(cleaner) + 청킹(chunker) 처리.

정제/청킹 결과는 원본 컬렉션(config.MONGO_COLLECTION)을 건드리지 않고
별도 컬렉션(config.CLEANED_COLLECTION)에 새 문서로 저장한다.
원본 content와 처리 결과(clean_content, chunks)를 한 문서 안에 같이 담아
Atlas 등 웹 콘솔에서 전후 비교를 바로 볼 수 있게 구성.

임베딩/Qdrant 저장(다음 단계)은 여기서 다루지 않음.
"""

import sys
from datetime import datetime, timezone

from config.config_cilent import CLEANED_COLLECTION
from DB.mongo_client import get_collection
from preprocessing.cleaner import clean_text
from preprocessing.chunker import chunk_document


def preprocess_documents(keyword: str | None = None) -> list[dict]:
    """
    is_embedded=False인 문서를 대상으로 정제+청킹 수행.
    keyword가 주어지면 해당 키워드 문서만 처리.
    결과는 CLEANED_COLLECTION에 저장하고, 생성된 청크를 합쳐서 반환.
    """
    source_collection = get_collection()
    output_collection = get_collection(CLEANED_COLLECTION)

    query = {"is_embedded": False}
    if keyword:
        query["keyword"] = keyword

    all_chunks = []
    processed, skipped = 0, 0

    for doc in source_collection.find(query):
        raw_content = doc.get("content", "")
        if not raw_content.strip():
            skipped += 1
            continue

        clean_content = clean_text(raw_content)

        doc_for_chunking = dict(doc)
        doc_for_chunking["clean_content"] = clean_content
        chunks = chunk_document(doc_for_chunking)

        output_doc = {
            "_id": doc["_id"],
            "keyword": doc.get("keyword"),
            "source": doc.get("source"),
            "url": doc.get("url"),
            "title": doc.get("title"),
            "content": raw_content,
            "clean_content": clean_content,
            # Qdrant 임베딩 단계에서 페이로드로 그대로 쓸 수 있도록 chunk_document()가
            # 만든 필드(parent_id, source, keyword, url, title, published_date, crawled_at 등)를
            # 축약하지 않고 전부 저장한다.
            "chunks": chunks,
            "chunk_count": len(chunks),
            "processed_at": datetime.now(timezone.utc),
        }
        output_collection.replace_one({"_id": doc["_id"]}, output_doc, upsert=True)

        all_chunks.extend(chunks)
        processed += 1

    print(f"[전처리] 처리: {processed}개 문서 / 스킵(빈 본문): {skipped}개 / 생성된 청크: {len(all_chunks)}개")
    print(f"[전처리] 결과 저장 위치: '{CLEANED_COLLECTION}' 컬렉션")
    return all_chunks


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else None
    result_chunks = preprocess_documents(kw)

    if result_chunks:
        print("\n--- 미리보기 ---")
        for c in result_chunks[:3]:
            print(f"[{c['source']}] section={c['section_title']} len={len(c['text'])}")
            print(c["text"][:150])
            print()
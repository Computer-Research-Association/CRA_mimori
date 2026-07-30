"""
pipeline.py
cleaned_memes의 청크를 BGE-M3(dense+sparse)로 임베딩해 Qdrant에 적재.

진행 상태 추적:
  memes.is_embedded 필드를 그대로 재사용한다. cleaned_memes 문서의 _id는
  크롤러가 만든 memes의 _id와 동일하므로(preprocessing/pipeline.py 참고)
  별도 매핑 없이 그 _id로 memes.is_embedded를 갱신할 수 있다.

  - 문서 하나의 청크가 전부(dense+sparse) Qdrant에 upsert된 뒤에만 True로 표시.
  - 중간에 실패하면 False로 남아, 다음 실행에서 그 문서를 처음부터 다시 시도한다
    (청크 단위 부분 재개는 하지 않음 — 문서당 청크 수가 적어 재시도 비용이 낮음).

Point ID:
  Qdrant는 point id로 부호 없는 정수 또는 UUID 형식만 허용한다.
  청크의 자연스러운 키(parent_id + chunk_index)는 이 형식이 아니므로,
  uuid5로 결정론적 변환을 거친다 (같은 입력 -> 항상 같은 id -> 재실행 시 upsert가 덮어씀).
"""

import uuid

from qdrant_client.http import models

from config.config_cilent import (
    CLEANED_COLLECTION,
    EMBEDDING_BATCH_SIZE,
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
)
from DB.mongo_client import get_collection
from DB.drant_clitent import client, ensure_collection
from embedding.encoder import encode_batch

_ID_NAMESPACE = uuid.NAMESPACE_DNS


def _point_id(parent_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{parent_id}::{chunk_index}"))


def _to_sparse_vector(lexical_weights: dict) -> models.SparseVector:
    indices = [int(token_id) for token_id in lexical_weights.keys()]
    values = [float(weight) for weight in lexical_weights.values()]
    return models.SparseVector(indices=indices, values=values)


def _build_points(chunks: list[dict]) -> list[models.PointStruct]:
    points = []
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[start:start + EMBEDDING_BATCH_SIZE]
        dense_vecs, lexical_weights = encode_batch([c["text"] for c in batch])

        for chunk, dense, sparse in zip(batch, dense_vecs, lexical_weights):
            parent_id = str(chunk["parent_id"])
            points.append(models.PointStruct(
                id=_point_id(parent_id, chunk["chunk_index"]),
                vector={
                    QDRANT_DENSE_VECTOR_NAME: dense,
                    QDRANT_SPARSE_VECTOR_NAME: _to_sparse_vector(sparse),
                },
                payload={
                    "parent_id": parent_id,
                    "chunk_index": chunk["chunk_index"],
                    "text": chunk["text"],
                    "source": chunk.get("source"),
                    "keyword": chunk.get("keyword"),
                    "url": chunk.get("url"),
                    "title": chunk.get("title"),
                    "section_title": chunk.get("section_title"),
                    "published_date": chunk.get("published_date"),
                    "crawled_at": chunk.get("crawled_at"),
                    "is_relevant": chunk.get("is_relevant"),
                    "relevance_position": chunk.get("relevance_position"),
                },
            ))
    return points


def embed_documents(keyword: str | None = None) -> dict:
    """
    memes.is_embedded=False인 문서 중 cleaned_memes에 이미 청킹돼 있는 것을 대상으로
    임베딩 + Qdrant 적재 수행. keyword가 주어지면 해당 키워드 문서만 처리.
    """
    ensure_collection()

    memes = get_collection()
    cleaned = get_collection(CLEANED_COLLECTION)

    query = {"is_embedded": False}
    if keyword:
        query["keyword"] = keyword

    pending_ids = [d["_id"] for d in memes.find(query, {"_id": 1})]
    if not pending_ids:
        print("[임베딩] 처리할 문서가 없습니다.")
        return {"documents": 0, "chunks": 0}

    docs = list(cleaned.find({"_id": {"$in": pending_ids}}))
    not_yet_chunked = len(pending_ids) - len(docs)

    doc_count, chunk_count, failed = 0, 0, 0

    for doc in docs:
        chunks = doc.get("chunks", [])
        if not chunks:
            # 본문이 비어 청크가 아예 없는 문서 -> 더 처리할 게 없으니 완료 처리
            memes.update_one({"_id": doc["_id"]}, {"$set": {"is_embedded": True}})
            doc_count += 1
            continue

        try:
            points = _build_points(chunks)
            client.upsert(collection_name=QDRANT_COLLECTION, points=points)
        except Exception as e:
            print(f"[임베딩] 실패 ({doc.get('title')}): {e}")
            failed += 1
            continue  # is_embedded는 False로 남아 다음 실행에서 재시도

        memes.update_one({"_id": doc["_id"]}, {"$set": {"is_embedded": True}})
        doc_count += 1
        chunk_count += len(points)
        print(f"[임베딩] {doc.get('title')} — 청크 {len(points)}개 적재 완료")

    print(
        f"[임베딩] 완료: 문서 {doc_count}개 / 청크 {chunk_count}개 "
        f"/ 실패 {failed}개 / 아직 청킹 안 됨(스킵) {not_yet_chunked}개"
    )
    return {"documents": doc_count, "chunks": chunk_count, "failed": failed}

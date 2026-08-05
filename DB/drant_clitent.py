import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models

from config.config_cilent import (
    EMBEDDING_DENSE_DIM,
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
    QDRANT_HOST,
    QDRANT_PORT,
)


load_dotenv()

client = QdrantClient(
    host=QDRANT_HOST,
    port=QDRANT_PORT
)


def ensure_collection(name: str = QDRANT_COLLECTION) -> None:
    """
    dense(코사인) + sparse 벡터를 담는 컬렉션이 없으면 생성.
    이미 있으면 그대로 둠 (재실행해도 안전).

    필터에 쓰이는 payload 인덱스도 함께 보장한다 — Qdrant는 인덱스가 없는
    필드로 scroll/filter하면 400 에러를 내므로, 컬렉션 신규/기존 여부와
    무관하게 매번 확인한다 (create_payload_index는 이미 있어도 안전하게
    재호출 가능).
    """
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={
                QDRANT_DENSE_VECTOR_NAME: models.VectorParams(
                    size=EMBEDDING_DENSE_DIM,
                    distance=models.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                QDRANT_SPARSE_VECTOR_NAME: models.SparseVectorParams(),
            },
        )

    # 필터에 쓰이는 payload 필드는 전부 인덱스를 만들어 둔다.
    #   keyword   : RAG 검색 필터
    #   source    : RAG 소스 제한 필터
    #   parent_id : 재적재 시 기존 point 삭제 (embedding/pipeline.py)
    # create_payload_index는 이미 있어도 안전하게 재호출 가능하다.
    for field_name in ("keyword", "source", "parent_id"):
        client.create_payload_index(
            collection_name=name,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

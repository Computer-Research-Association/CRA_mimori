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

    keyword 필드의 payload 인덱스도 함께 보장한다 — Qdrant는 인덱스가 없는
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

    client.create_payload_index(
        collection_name=name,
        field_name="keyword",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=name,
        field_name="parent_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    # is_relevant 백필 완료 시 필터 조건으로 쓰이므로 인덱스 미리 보장
    client.create_payload_index(
        collection_name=name,
        field_name="is_relevant",
        field_schema=models.PayloadSchemaType.BOOL,
    )

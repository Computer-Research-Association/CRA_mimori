import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models

from config.config_cilent import (
    EMBEDDING_DENSE_DIM,
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
)

load_dotenv()

client = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)


def ensure_collection(name: str = QDRANT_COLLECTION) -> None:
    """
    dense(코사인) + sparse 벡터를 담는 컬렉션이 없으면 생성.
    이미 있으면 그대로 둠 (재실행해도 안전).
    """
    if client.collection_exists(name):
        return

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
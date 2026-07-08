"""
parent_lookup.py
청크의 parent_id(= memes 컬렉션 _id)로 원본 문서를 재조회하는 헬퍼.

Qdrant 검색 후 top-k 청크를 얻었을 때 LLM에 넘길 원본 문서가 필요하면
이 모듈로 한 번에 가져온다. $in 쿼리로 N번 왕복을 1번으로 줄임.
"""

from config.config_cilent import CLEANED_COLLECTION
from DB.mongo_client import get_collection


def get_parent(parent_id) -> dict | None:
    """단건 조회. 없으면 None."""
    return get_collection(CLEANED_COLLECTION).find_one({"_id": parent_id})


def get_parents(parent_ids) -> dict:
    """
    복수 조회. $in으로 한 번에 가져와 {_id: doc} 딕셔너리로 반환.
    Qdrant top-k 결과가 여러 문서에 걸칠 때 N번 쿼리를 방지.
    """
    ids = list(parent_ids)
    docs = get_collection(CLEANED_COLLECTION).find({"_id": {"$in": ids}})
    return {doc["_id"]: doc for doc in docs}

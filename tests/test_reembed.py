"""
재적재 시 고아 청크가 남지 않는지 검증.

QdrantClient(":memory:")를 쓴다 — 서버도, 포트도, 도커도, EC2도 필요 없다.
프로젝트가 실제로 쓰는 기능(dense+sparse 네임드 벡터, uuid5 결정론적 id,
필터 delete)이 인메모리 모드에서 전부 동작함을 확인했다.

실행:  uv run python tests/test_reembed.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.http import models

COLL = "test_chunks"
NS = uuid.NAMESPACE_DNS


def _point_id(parent_id: str, idx: int) -> str:
    # embedding/pipeline.py:_point_id 와 같은 규칙
    return str(uuid.uuid5(NS, f"{parent_id}::{idx}"))


def _make_client() -> QdrantClient:
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=COLL,
        vectors_config={"dense": models.VectorParams(size=4, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    return client


def _points(parent_id: str, n: int, tag: str):
    return [
        models.PointStruct(
            id=_point_id(parent_id, i),
            vector={
                "dense": [0.1 * (i + 1), 0.2, 0.3, 0.4],
                "sparse": models.SparseVector(indices=[i, i + 10], values=[0.9, 0.5]),
            },
            payload={"parent_id": parent_id, "chunk_index": i, "text": f"{tag} 청크 {i}"},
        )
        for i in range(n)
    ]


def _texts(client) -> list[str]:
    points, _ = client.scroll(COLL, limit=100, with_payload=True)
    return sorted(p.payload["text"] for p in points)


def test_delete_없이_재적재하면_고아가_남는다():
    """수정 전 동작을 고정해둔다 — 이 문제가 실재함을 증명하는 테스트."""
    client = _make_client()
    client.upsert(collection_name=COLL, points=_points("docA", 8, "구버전"))
    client.upsert(collection_name=COLL, points=_points("docA", 5, "신버전"))

    assert client.count(COLL).count == 8, "고아가 안 생겼다면 전제가 틀린 것"
    assert any("구버전" in t for t in _texts(client)), _texts(client)
    print("[OK] delete 없으면 고아 3개 잔류 (5개만 넣었는데 count=8)")


def test_delete_후_재적재하면_고아가_없다():
    from embedding.pipeline import _build_delete_filter

    client = _make_client()
    client.upsert(collection_name=COLL, points=_points("docA", 8, "구버전"))

    client.delete(
        collection_name=COLL,
        points_selector=models.FilterSelector(filter=_build_delete_filter("docA")),
    )
    client.upsert(collection_name=COLL, points=_points("docA", 5, "신버전"))

    assert client.count(COLL).count == 5, client.count(COLL).count
    assert not any("구버전" in t for t in _texts(client)), _texts(client)
    print("[OK] delete 후 재적재하면 고아 없음 (count=5)")


def test_다른_문서는_지워지지_않는다():
    from embedding.pipeline import _build_delete_filter

    client = _make_client()
    client.upsert(collection_name=COLL, points=_points("docA", 3, "A"))
    client.upsert(collection_name=COLL, points=_points("docB", 3, "B"))

    client.delete(
        collection_name=COLL,
        points_selector=models.FilterSelector(filter=_build_delete_filter("docA")),
    )

    remaining = _texts(client)
    assert len(remaining) == 3, remaining
    assert all(t.startswith("B") for t in remaining), remaining
    print("[OK] 필터가 대상 문서만 지운다")


def test_청크가_0개가_되어도_고아가_남지_않는다():
    """청커 최소 길이 상향 등으로 문서의 청크가 전부 사라지는 경우.
    이때도 기존 point 를 지워야 옛 텍스트가 검색에 남지 않는다."""
    from embedding.pipeline import _build_delete_filter

    client = _make_client()
    client.upsert(collection_name=COLL, points=_points("docA", 8, "구버전"))

    # 새 청크가 0개 -> upsert 할 것이 없고, delete 만 수행된다
    client.delete(
        collection_name=COLL,
        points_selector=models.FilterSelector(filter=_build_delete_filter("docA")),
    )

    assert client.count(COLL).count == 0, client.count(COLL).count
    assert _texts(client) == [], _texts(client)
    print("[OK] 청크 0개가 되어도 고아 없음 (count=0)")


if __name__ == "__main__":
    test_delete_없이_재적재하면_고아가_남는다()
    test_delete_후_재적재하면_고아가_없다()
    test_다른_문서는_지워지지_않는다()
    test_청크가_0개가_되어도_고아가_남지_않는다()
    print("\nALL PASS ✅")

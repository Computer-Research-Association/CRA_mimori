"""
embedding/pipeline.py의 적재 시점 근접중복 제거 검증.

analysis/langchain_playground.ipynb가 RAG 질의마다 반복 계산하던 difflib 기반
근접중복 판정(SequenceMatcher.ratio() >= 0.8)을 적재 시점으로 옮긴다 — 한 번만
걸러두면 이후 모든 질의가 이 계산을 반복하지 않아도 된다.

_filter_near_duplicates()는 순수 함수(외부 의존 없음). _fetch_existing_texts()는
인메모리 Qdrant로 검증(서버/도커 불필요, test_reembed.py와 동일 패턴).

실행:  uv run python tests/test_embedding_near_dup.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.http import models

from embedding.pipeline import _filter_near_duplicates, _fetch_existing_texts


def test_기존_텍스트와_다르면_전부_유지된다():
    chunks = [{"text": "야르는 신나는 감탄사다"}, {"text": "쌰갈은 욕설을 순화한 표현이다"}]
    kept = _filter_near_duplicates(chunks, existing_texts=[])
    assert len(kept) == 2, kept
    print("[OK] 기존 텍스트 없으면 전부 유지")


def test_기존_텍스트와_거의_같으면_제외된다():
    existing = ["오늘 진짜 야르한 하루였다 정말 신나는 하루였음"]
    chunks = [{"text": "오늘 진짜 야르한 하루였다 정말 신나는 하루였음!!"}]  # 거의 동일(느낌표만 추가)
    kept = _filter_near_duplicates(chunks, existing_texts=existing)
    assert kept == [], kept
    print("[OK] 근접중복(0.8 이상) 청크 제외")


def test_같은_배치_안에서도_서로_중복이면_뒤엣것만_제외된다():
    # 이번 실행에서 함께 들어온 두 문서가 서로 근접중복인 경우(예: 크로스포스팅)도
    # 걸러야 한다 — 기존 Qdrant 데이터와 비교하는 것만으론 부족하다.
    chunks = [
        {"text": "오늘 진짜 야르한 하루였다 정말 신나는 하루였음"},
        {"text": "오늘 진짜 야르한 하루였다 정말 신나는 하루였음!!"},
    ]
    kept = _filter_near_duplicates(chunks, existing_texts=[])
    assert len(kept) == 1, kept
    assert kept[0]["text"] == chunks[0]["text"], kept
    print("[OK] 같은 배치 내 근접중복도 하나만 유지")


def test_임계값_미만이면_유지된다():
    existing = ["완전히 다른 내용의 문장입니다"]
    chunks = [{"text": "야르는 신나는 감탄사다 전혀 다른 얘기"}]
    kept = _filter_near_duplicates(chunks, existing_texts=existing)
    assert len(kept) == 1, kept
    print("[OK] 유사도 0.8 미만이면 유지")


def test_기존_청크_텍스트를_인메모리_Qdrant에서_가져온다():
    coll = "test_dup_fetch"
    qclient = QdrantClient(":memory:")
    qclient.create_collection(
        collection_name=coll,
        vectors_config={"dense": models.VectorParams(size=4, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    qclient.upsert(collection_name=coll, points=[
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector={"dense": [0.1, 0.2, 0.3, 0.4], "sparse": models.SparseVector(indices=[0], values=[1.0])},
            payload={"keyword": "야르", "text": "야르 관련 기존 청크"},
        ),
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector={"dense": [0.1, 0.2, 0.3, 0.4], "sparse": models.SparseVector(indices=[0], values=[1.0])},
            payload={"keyword": "쌰갈", "text": "쌰갈 관련 청크 — 다른 키워드라 안 나와야 함"},
        ),
    ])

    texts = _fetch_existing_texts(qclient, coll, "야르")
    assert texts == ["야르 관련 기존 청크"], texts
    print("[OK] 같은 키워드 기존 청크만 조회됨")


if __name__ == "__main__":
    test_기존_텍스트와_다르면_전부_유지된다()
    test_기존_텍스트와_거의_같으면_제외된다()
    test_같은_배치_안에서도_서로_중복이면_뒤엣것만_제외된다()
    test_임계값_미만이면_유지된다()
    test_기존_청크_텍스트를_인메모리_Qdrant에서_가져온다()
    print("\nALL PASS ✅")

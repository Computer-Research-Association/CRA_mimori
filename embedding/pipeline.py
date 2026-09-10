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

import difflib
import uuid

from qdrant_client.http import models

from config.config_cilent import (
    CLEANED_COLLECTION,
    EMBEDDING_BATCH_SIZE,
    LLM_REQUESTS_COLLECTION,
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
)
from DB.mongo_client import get_collection
from DB.drant_clitent import ensure_collection, get_client
from embedding.encoder import encode_batch
from perf_log import accumulate

_ID_NAMESPACE = uuid.NAMESPACE_DNS

# analysis/langchain_playground.ipynb의 RAG 근접중복 판정과 동일 기준(0.8).
# 그쪽은 질의마다 반복 계산하는데, 여기서는 적재 시점에 한 번만 걸러서 이후
# 모든 질의가 이 계산을 다시 안 해도 되게 한다.
_NEAR_DUP_THRESHOLD = 0.8
_NEAR_DUP_FETCH_LIMIT = 500  # 키워드당 비교 대상 상한. 그 이상 기존 청크가 쌓인
# 키워드는 이 상한을 넘는 옛 청크와는 비교하지 않는다 — scroll 페이지네이션 없이
# 첫 배치만 보는 실용적 절충(전량 비교는 키워드당 청크가 아주 많아지면 느려짐).


def _fetch_existing_texts(qdrant_client, collection: str, keyword: str) -> list[str]:
    """Qdrant에서 같은 keyword의 기존 청크 텍스트를 가져온다(근접중복 비교용)."""
    points, _ = qdrant_client.scroll(
        collection_name=collection,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
        ),
        limit=_NEAR_DUP_FETCH_LIMIT,
        with_payload=["text"],
    )
    return [p.payload["text"] for p in points if p.payload.get("text")]


def _filter_near_duplicates(chunks: list[dict], existing_texts: list[str]) -> list[dict]:
    """기존 청크(existing_texts) 및 같은 배치 내 앞선 청크와 근접중복(문자 유사도
    0.8 이상)인 청크를 제외한다. 같은 배치 안에서 서로 근접중복인 경우(예: 크로스
    포스팅으로 여러 문서에 같은 글이 실린 경우)도 걸러야 하므로, 채택된 청크의
    텍스트를 accepted에 계속 누적하며 비교한다.
    """
    accepted = list(existing_texts)
    kept = []
    for chunk in chunks:
        text = chunk["text"].strip()
        if any(
            difflib.SequenceMatcher(None, text, other).ratio() >= _NEAR_DUP_THRESHOLD
            for other in accepted
        ):
            continue
        accepted.append(text)
        kept.append(chunk)
    return kept


def _point_id(parent_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{parent_id}::{chunk_index}"))


def _build_delete_filter(parent_id: str) -> models.Filter:
    """이 문서에 속한 모든 청크 point를 고르는 필터."""
    return models.Filter(must=[
        models.FieldCondition(key="parent_id", match=models.MatchValue(value=parent_id))
    ])


def _delete_existing_points(parent_id: str) -> None:
    """재적재 전에 이 문서의 기존 point를 전부 지운다.

    point id가 uuid5(parent_id::chunk_index)로 결정론적이라 같은 인덱스는 upsert가
    덮어쓰지만, 청크 수가 줄면 뒤쪽 인덱스의 옛 point가 그대로 남는다. 그 point는
    keyword payload를 멀쩡히 갖고 있어 RAG 검색에 계속 잡힌다 — "저품질 청크 제거"
    작업이 오히려 저품질 청크를 고착시키게 된다.

    '재처리할 때만'이 아니라 '항상' 지운다. 조건부로 만들면 언젠가 조건이 틀린다.
    항상 지우고 넣으면 몇 번을 돌려도 상태가 같아진다(멱등). 비용은 문서당 delete 1회다.
    """
    get_client().delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.FilterSelector(filter=_build_delete_filter(parent_id)),
    )


def _to_sparse_vector(lexical_weights: dict) -> models.SparseVector:
    indices = [int(token_id) for token_id in lexical_weights.keys()]
    values = [float(weight) for weight in lexical_weights.values()]
    return models.SparseVector(indices=indices, values=values)


def _encoding_text(chunk: dict) -> str:
    """인코딩(벡터화)에 넣을 텍스트. payload에 저장되는 chunk["text"]는 절대 안 건드림.

    section_title이 있는 청크(나무위키 섹션 구조)는 제목/섹션제목을 앞에 붙인다 —
    "유래" 섹션이 "1990년대부터 사용되기 시작했다"처럼 대상을 생략하고 시작하는
    경우가 흔해서, 문맥 없이 그 텍스트만 인코딩하면 임베딩이 무엇에 대한 얘기인지
    모른다. section_title이 없는 소스(커뮤니티 글 등)는 원문 그대로 인코딩한다 —
    거기선 제목이 본문과 겹치는 경우가 많아 프리픽스 이득이 검증되지 않았다.
    """
    section_title = chunk.get("section_title")
    if not section_title:
        return chunk["text"]
    title = chunk.get("title") or ""
    prefix = f"{title} - {section_title}" if title else section_title
    return f"{prefix}\n{chunk['text']}"


def _build_points(chunks: list[dict]) -> list[models.PointStruct]:
    points = []
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[start:start + EMBEDDING_BATCH_SIZE]
        with accumulate("임베딩:BGE-M3 인코딩"):
            dense_vecs, lexical_weights = encode_batch([_encoding_text(c) for c in batch])

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
                    "content_type": chunk.get("content_type"),
                    "published_date": chunk.get("published_date"),
                    "crawled_at": chunk.get("crawled_at"),
                    "is_relevant": chunk.get("is_relevant"),
                    "relevance_position": chunk.get("relevance_position"),
                    "relevance_match_count": chunk.get("relevance_match_count"),
                },
            ))
    return points


def embed_documents(keyword: str | None = None) -> dict:
    """
    memes.is_embedded=False인 문서 중 cleaned_memes에 이미 청킹돼 있는 것을 대상으로
    임베딩 + Qdrant 적재 수행. keyword가 주어지면 해당 키워드 문서만 처리.
    """
    ensure_collection()
    client = get_client()

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

    doc_count, chunk_count, failed, near_dup_skipped = 0, 0, 0, 0
    # 이번 실행에서 실제로 is_embedded가 바뀐(=코퍼스가 바뀐) 키워드. 끝에 이 키워드들의
    # 캐시된 분석 결과(llm_requests)를 무효화해 다음 조회가 최신 코퍼스로 재분석하게 한다
    # — crawl_request_worker.py가 온디맨드 경로에서 하는 것과 같은 이유, 여기선 배치
    # 경로(스케줄러가 매일 부르는 preprocess_embed_main.py)까지 빠짐없이 커버한다.
    affected_keywords: set[str] = set()
    # 키워드별로 한 번만 Qdrant에서 기존 청크 텍스트를 가져와 이번 실행 내내 재사용한다
    # (문서마다 다시 조회하지 않음). 이번 실행에서 채택된 청크도 계속 누적해서, 같은
    # 실행 안에서 여러 문서가 서로 근접중복인 경우(크로스포스팅)도 걸러진다.
    keyword_texts_cache: dict[str, list[str]] = {}

    for doc in docs:
        raw_chunks = doc.get("chunks", [])
        doc_keyword = doc.get("keyword")

        if raw_chunks:
            if doc_keyword not in keyword_texts_cache:
                with accumulate("임베딩:Qdrant 기존청크 조회(scroll)"):
                    keyword_texts_cache[doc_keyword] = _fetch_existing_texts(client, QDRANT_COLLECTION, doc_keyword)
            with accumulate("임베딩:근접중복 필터(difflib)"):
                chunks = _filter_near_duplicates(raw_chunks, keyword_texts_cache[doc_keyword])
            near_dup_skipped += len(raw_chunks) - len(chunks)
            keyword_texts_cache[doc_keyword].extend(c["text"].strip() for c in chunks)
        else:
            chunks = raw_chunks

        if not chunks:
            # 본문이 비어 청크가 아예 없거나, 근접중복으로 전부 걸러진 문서.
            # 이전 실행에서 청크가 있었다면 그 point 들이 그대로 남으므로 여기서도 지운다
            # (예: 청커 최소 길이 상향, 또는 근접중복 필터로 이 문서의 모든 청크가 걸러진 경우).
            try:
                _delete_existing_points(str(doc["_id"]))
            except Exception as e:
                print(f"[임베딩] 기존 point 삭제 실패 ({doc.get('title')}): {e}")
                failed += 1
                continue  # is_embedded는 False로 남아 다음 실행에서 재시도
            memes.update_one({"_id": doc["_id"]}, {"$set": {"is_embedded": True}})
            doc_count += 1
            if doc_keyword:
                affected_keywords.add(doc_keyword)
            continue

        # _build_points()를 먼저 실행하는 것은 의도된 설계다. 임베딩 모델(가장 실패할 수 있는
        # 단계)을 먼저 호출한 뒤 기존 point를 지우므로, 실패해도 기존 데이터가 보존된다.
        # 다만 delete 후 upsert 사이에 창이 있어 delete는 성공하되 upsert가 실패하면 점이
        # 손실된다. 자동 재시도를 위해 is_embedded가 False로 유지되므로 다음 실행에서 회복된다
        # (셀프힐링). 현재는 아무도 is_embedded를 False로 리셋하지 않아 휴면이나, 3단계 백필이
        # 추가되면 현실화된다. 그전에 순서 재검토 또는 재시도 로직 추가를 고려하자.
        try:
            points = _build_points(chunks)
            with accumulate("임베딩:Qdrant 기존point 삭제"):
                _delete_existing_points(str(doc["_id"]))
            with accumulate("임베딩:Qdrant upsert"):
                client.upsert(collection_name=QDRANT_COLLECTION, points=points)
        except Exception as e:
            print(f"[임베딩] 실패 ({doc.get('title')}): {e}")
            failed += 1
            continue  # is_embedded는 False로 남아 다음 실행에서 재시도

        with accumulate("임베딩:Mongo is_embedded 갱신"):
            memes.update_one({"_id": doc["_id"]}, {"$set": {"is_embedded": True}})
        doc_count += 1
        chunk_count += len(points)
        if doc_keyword:
            affected_keywords.add(doc_keyword)
        print(f"[임베딩] {doc.get('title')} — 청크 {len(points)}개 적재 완료")

    print(
        f"[임베딩] 완료: 문서 {doc_count}개 / 청크 {chunk_count}개 "
        f"/ 실패 {failed}개 / 아직 청킹 안 됨(스킵) {not_yet_chunked}개 "
        f"/ 근접중복 제외 {near_dup_skipped}개"
    )

    if affected_keywords:
        llm_requests = get_collection(LLM_REQUESTS_COLLECTION)
        llm_requests.delete_many({"keyword": {"$in": list(affected_keywords)}, "status": "done"})
        print(f"[임베딩] 코퍼스 변경으로 분석 캐시 무효화: {', '.join(sorted(affected_keywords))}")

    return {
        "documents": doc_count,
        "chunks": chunk_count,
        "failed": failed,
        "near_dup_skipped": near_dup_skipped,
    }

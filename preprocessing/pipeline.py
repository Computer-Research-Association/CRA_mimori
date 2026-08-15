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
from perf_log import accumulate
from preprocessing.cleaner import clean_text
from preprocessing.chunker import chunk_document
from quality_test.matching import find_keyword


def _attach_relevance(title: str, keyword: str, clean_content: str, chunks: list[dict]) -> None:
    """문서 전체(제목 + 정제된 본문 전체)를 한 번 판정해 그 결과를 모든 청크에 동일하게 붙인다.

    반드시 clean_content(청킹 이전의 정제 전문)로 판정해야 한다 — 살아남은 청크만
    이어붙이면, 키워드가 든 부분이 마침 30자 미만이라 청킹 단계에서 버려졌을 때
    그 텍스트가 사라져 judge가 잘못된 판정을 내린다(예: 본문 "야르~"가 30자
    미만이라 버려지고 댓글 청크만 남으면, 원문엔 키워드가 있는데도 비관련으로
    오판됨). clean_content는 청킹 임계값과 무관하게 항상 전문이 보존되므로
    이 문제가 구조적으로 생기지 않는다.

    청크 단위로 따로 판정하지 않는 이유: 관련 있는 문서라도 모든 청크가
    키워드를 반복하지는 않는다(quality-test-usage.md §8). 문서 단위로 한 번만
    판정해야 그런 청크가 잘못 걸러지지 않는다.
    """
    if not chunks:
        return
    match = find_keyword(keyword, title, clean_content)
    for chunk in chunks:
        chunk["is_relevant"] = match.matched
        chunk["relevance_position"] = match.position
        chunk["relevance_match_count"] = match.count


def process_one(doc: dict) -> tuple[str, list[dict]]:
    """문서 하나를 정제 + 청킹 + 관련성 판정한다. DB 접촉 없음.

    Mongo 경로(preprocess_documents)와 fixture 경로(quality_test/runner.py)가
    이 함수를 함께 쓴다 — 측정 대상이 프로덕션 코드와 다른 것이 되지 않도록
    한쪽만 고쳐질 수 없게 만든 장치다.

    반환: (clean_content, chunks). 각 청크에는 is_relevant/relevance_position/
    relevance_match_count가 문서 단위 판정 결과로 동일하게 붙는다.
    """
    with accumulate("전처리:정제(clean_text)"):
        clean_content = clean_text(doc.get("content", ""))
    with accumulate("전처리:청킹(chunk_document)"):
        chunks = chunk_document({**doc, "clean_content": clean_content})
    with accumulate("전처리:관련성판정(find_keyword)"):
        _attach_relevance(doc.get("title") or "", doc.get("keyword") or "", clean_content, chunks)
    return clean_content, chunks


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

        clean_content, chunks = process_one(doc)

        # process_one()이 이미 find_keyword(clean_content 기준)로 모든 청크에 동일한
        # is_relevant/relevance_position/relevance_match_count를 붙였다. 문서 단위
        # 최상위 필드는 그 결과를 그대로 재사용한다 — 별도 판정기로 다시 계산하면
        # (예전 judge_doc처럼 청크 재조합 텍스트를 보는 다른 알고리즘) is_relevant와
        # relevance_match_count가 서로 다른 근거로 계산되어 어긋날 수 있다.
        if chunks:
            is_relevant = chunks[0]["is_relevant"]
            relevance_position = chunks[0]["relevance_position"]
        else:
            match = find_keyword(doc.get("keyword") or "", doc.get("title") or "", clean_content)
            is_relevant, relevance_position = match.matched, match.position

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
            "is_relevant": is_relevant,
            "relevance_position": relevance_position,
            "processed_at": datetime.now(timezone.utc),
        }
        with accumulate("전처리:Mongo쓰기(replace_one)"):
            output_collection.replace_one({"_id": doc["_id"]}, output_doc, upsert=True)

        all_chunks.extend(chunks)
        processed += 1

    print(f"[전처리] 처리: {processed}개 문서 / 스킵(빈 본문): {skipped}개 / 생성된 청크: {len(all_chunks)}개")
    print(f"[전처리] 결과 저장 위치: '{CLEANED_COLLECTION}' 컬렉션")
    return all_chunks


def _build_reprocess_query(keyword: str | None, source: str | None, all_sources: bool = False) -> dict:
    """재처리 대상을 고르는 쿼리를 만든다.

    keyword/source가 둘 다 없으면 ValueError를 던진다 — memes 전체를 실수로
    재처리 대상으로 돌리는 사고를 막기 위한 안전장치("깜빡하고 범위를 안 줌"과
    "정말 전체를 원함"을 구분해야 하므로).

    정말 전체를 재처리하고 싶다면 all_sources=True를 명시적으로 준다 — 이러면
    keyword/source가 없어도 에러 없이 빈 쿼리(전체 대상)를 반환한다. all_sources=True와
    keyword를 같이 주면 "전체 소스에서 이 키워드만"으로 좁혀진다.
    """
    if all_sources:
        query: dict = {}
        if keyword:
            query["keyword"] = keyword
        return query

    if not keyword and not source:
        raise ValueError(
            "keyword 또는 source 중 최소 하나는 지정해야 합니다 (전체 재처리 사고 방지). "
            "정말 전체를 원하면 all_sources=True를 명시하세요."
        )
    query = {}
    if keyword:
        query["keyword"] = keyword
    if source:
        query["source"] = source
    return query


def reset_for_reprocessing(
    keyword: str | None = None, source: str | None = None, all_sources: bool = False
) -> int:
    """memes 문서의 is_embedded를 다시 False로 되돌려 재처리 대상에 포함시킨다.

    다음 preprocess_embed_job 실행에서 정제 → 청킹 → judge → 임베딩이 전부
    이번 세션에 개선된 로직으로 다시 돈다. 여러 번 재실행해도 안전하다 —
    embedding/pipeline.py의 _delete_existing_points()가 upsert 전에 항상 기존
    point를 먼저 지우므로(Task 8) 고아 point가 남지 않는다.

    반환값은 실제로 리셋된 문서 수(이미 is_embedded=False였던 문서는 modified_count에
    안 잡힘).
    """
    query = _build_reprocess_query(keyword, source, all_sources)
    result = get_collection().update_many(query, {"$set": {"is_embedded": False}})
    return result.modified_count


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else None
    result_chunks = preprocess_documents(kw)

    if result_chunks:
        print("\n--- 미리보기 ---")
        for c in result_chunks[:3]:
            print(f"[{c['source']}] section={c['section_title']} len={len(c['text'])}")
            print(c["text"][:150])
            print()

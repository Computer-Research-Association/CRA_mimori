"""
purge_irrelevant.py
judge가 문서 단위로 완전히 비관련(is_relevant=False) 판정한 문서를 cleaned_memes +
Qdrant에서 제거한다. 기본은 미리보기(dry-run)만 하고 아무것도 안 지운다 — 실제 삭제는
--confirm을 명시해야 실행된다.

전제 조건: judge는 preprocess_documents()가 실제로 한 번 돈 문서에만 계산돼 있다.
이 스크립트를 돌리기 전에 (1) 이 브랜치를 배포하고 (2) 재처리 트리거로 대상 문서를
is_embedded=False로 되돌리고 (3) preprocess_embed_main.py(또는 스케줄러)가 실제로
한 번 더 실행돼서 judge가 계산돼 있어야 한다. 그 전에는 대상이 0건으로 나온다
(에러가 아니라 정상 — 아직 판정 자체가 없다는 뜻).

삭제 범위 (기본, --include-raw 없이):
  - Qdrant의 해당 문서 청크 포인트 전부 (embedding/pipeline.py의 삭제 필터 재사용)
  - cleaned_memes의 처리 결과 문서
  - memes의 원본 크롤링 문서는 남기되, judge_excluded=True 플래그를 남겨
    앞으로의 재처리 루프에서 계속 제외되게 함(원본 자체는 안 건드림 — 나중에 judge가
    개선되면 사람이 다시 판단할 여지를 남겨두는 것).

--include-raw를 주면 memes 원본 문서까지 완전히 삭제한다. 되돌릴 수 없다.

사용법:
  # 1. 미리보기 (아무것도 안 지움, 항상 먼저 이걸로 확인)
  uv run python scripts/purge_irrelevant.py
  uv run python scripts/purge_irrelevant.py --keyword 야르

  # 2. 실제 삭제 (cleaned_memes + Qdrant만, 원본 memes는 보존 + judge_excluded 플래그)
  uv run python scripts/purge_irrelevant.py --confirm

  # 3. 원본까지 완전 삭제 (되돌릴 수 없음)
  uv run python scripts/purge_irrelevant.py --confirm --include-raw
"""
import argparse
import sys

sys.path.insert(0, ".")

from config.config_cilent import CLEANED_COLLECTION
from DB.mongo_client import get_collection
from embedding.pipeline import _delete_existing_points


def _is_fully_irrelevant(doc: dict) -> bool:
    """cleaned_memes 문서 하나가 judge에 의해 완전히 비관련 판정됐는지 확인.

    청크가 있고 전부 is_relevant=False여야 True. 청크가 없거나(판정 자체가 안 됨)
    is_relevant 필드가 없는 문서(judge 미실행분)는 False — 삭제 대상이 아니다.
    """
    chunks = doc.get("chunks", [])
    return bool(chunks) and all(c.get("is_relevant") is False for c in chunks)


def find_candidates(keyword: str | None = None, source: str | None = None) -> list[dict]:
    """cleaned_memes에서 완전히 비관련 판정된 문서 목록을 찾는다."""
    query: dict = {}
    if keyword:
        query["keyword"] = keyword
    if source:
        query["source"] = source

    cleaned = get_collection(CLEANED_COLLECTION)
    return [doc for doc in cleaned.find(query) if _is_fully_irrelevant(doc)]


def purge(candidates: list[dict], include_raw: bool) -> dict:
    """실제 삭제 실행. candidates는 find_candidates()의 결과."""
    memes = get_collection()
    cleaned = get_collection(CLEANED_COLLECTION)

    deleted_cleaned, deleted_raw, failed = 0, 0, 0

    for doc in candidates:
        parent_id = str(doc["_id"])
        try:
            _delete_existing_points(parent_id)  # embedding/pipeline.py의 기존 삭제 헬퍼 재사용
            cleaned.delete_one({"_id": doc["_id"]})
            deleted_cleaned += 1

            if include_raw:
                memes.delete_one({"_id": doc["_id"]})
                deleted_raw += 1
            else:
                memes.update_one({"_id": doc["_id"]}, {"$set": {"judge_excluded": True}})
        except Exception as e:
            print(f"[실패] {doc.get('title')!r}: {e}")
            failed += 1

    return {"deleted_cleaned": deleted_cleaned, "deleted_raw": deleted_raw, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="judge가 비관련 판정한 문서를 cleaned_memes+Qdrant에서 제거")
    parser.add_argument("--keyword", default=None, help="이 키워드만 대상")
    parser.add_argument("--source", default=None, help="이 소스만 대상")
    parser.add_argument("--confirm", action="store_true", help="실제로 삭제 실행(없으면 미리보기만)")
    parser.add_argument("--include-raw", action="store_true", help="memes 원본까지 완전 삭제(되돌릴 수 없음)")
    args = parser.parse_args()

    candidates = find_candidates(keyword=args.keyword, source=args.source)
    print(f"판정 대상: {len(candidates)}건 (keyword={args.keyword!r}, source={args.source!r})")

    if not candidates:
        print("삭제할 문서가 없습니다. judge가 아직 이 문서들에 안 돌았을 수 있습니다 —")
        print("배포 -> 재처리 트리거 -> preprocess_embed_main.py 실행 순서를 확인하세요.")
        sys.exit(0)

    print("\n--- 표본 (최대 10건) ---")
    for doc in candidates[:10]:
        print(f"  [{doc.get('source')}] keyword={doc.get('keyword')!r} title={doc.get('title')!r}")

    if not args.confirm:
        print(f"\n[미리보기 모드] 실제로 지우려면 --confirm을 추가하세요.")
        if not args.include_raw:
            print("(memes 원본은 보존되고 judge_excluded=True 플래그만 남습니다. 원본까지 지우려면 --include-raw 추가)")
        sys.exit(0)

    print(f"\n[실행] {len(candidates)}건 삭제 시작 (--include-raw={args.include_raw})...")
    result = purge(candidates, include_raw=args.include_raw)
    print(
        f"\n완료: cleaned_memes+Qdrant 삭제 {result['deleted_cleaned']}건 "
        f"/ memes 원본 삭제 {result['deleted_raw']}건 / 실패 {result['failed']}건"
    )

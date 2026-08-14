"""
admin_stats.py
관리 화면(KeywordManager 옆)에 보여줄 서버/데이터 현황을 한 번에 모아준다.
저장 공간을 컬렉션 단위로 직접 재는 API는 MongoDB 커뮤니티 에디션 드라이버에 없어서,
컬렉션별 문서 수 + 키워드별 문서 수로 대신한다 — 문서 수가 실질적으로 저장 공간과
비례하고, "어느 키워드가 무거운지"가 정리 대상을 고르는 데 더 실용적인 정보이기도 하다.
"""
import shutil

from config.config_cilent import (
    CLEANED_COLLECTION,
    CRAWL_REQUESTS_COLLECTION,
    HIDDEN_KEYWORDS_COLLECTION,
    LLM_REQUESTS_COLLECTION,
    MAX_BATCH_KEYWORDS,
    MONGO_COLLECTION,
    REJECT_COLLECTION,
    TREND_COLLECTION,
)

# 컬렉션 키(응답 필드명) -> 실제 컬렉션 이름. 순서가 곧 응답의 collection_counts 순서.
_COLLECTION_NAMES = {
    "memes": MONGO_COLLECTION,
    "cleaned_memes": CLEANED_COLLECTION,
    "trend_scores": TREND_COLLECTION,
    "crawl_requests": CRAWL_REQUESTS_COLLECTION,
    "hidden_keywords": HIDDEN_KEYWORDS_COLLECTION,
    "llm_requests": LLM_REQUESTS_COLLECTION,
    "crawl_rejects": REJECT_COLLECTION,
}


def _count_per_keyword_by_source(collection, keywords: list[str]) -> list[dict]:
    """collection에서 keywords 각각의 문서 수를, 출처(source)별로 나눠 한 번의 집계로 센다.

    반환: [{"keyword": ..., "total": ..., "by_source": {"dcinside": 12, ...}}, ...] (많은 순).
    "어느 키워드가 무거운지"뿐 아니라 "그 데이터가 어디서 왔는지"까지 봐야 정리 대상을
    제대로 고를 수 있다 — 예: 같은 100건이어도 출처 하나에 쏠려 있으면 그 출처가 죽었을 때
    타격이 크다는 뜻이다.
    """
    if not keywords:
        return []
    pipeline = [
        {"$match": {"keyword": {"$in": keywords}}},
        {"$group": {"_id": {"keyword": "$keyword", "source": "$source"}, "count": {"$sum": 1}}},
    ]
    by_keyword: dict[str, dict] = {}
    for row in collection.aggregate(pipeline):
        kw = row["_id"]["keyword"]
        source = row["_id"]["source"]
        entry = by_keyword.setdefault(kw, {"keyword": kw, "total": 0, "by_source": {}})
        entry["by_source"][source] = row["count"]
        entry["total"] += row["count"]

    rows = [by_keyword.get(kw, {"keyword": kw, "total": 0, "by_source": {}}) for kw in keywords]
    rows.sort(key=lambda row: -row["total"])
    return rows


def _disk_usage_stats(disk_usage_fn, path: str = "/") -> dict:
    """호스트(EC2) 디스크 용량/사용량. api 컨테이너에서 shutil.disk_usage("/")를 부르면
    되는데, 오버레이2(도커 기본 스토리지 드라이버)는 컨테이너 루트가 호스트 디스크를
    그대로 보므로 EC2 자체 디스크 사용량과 사실상 같은 값이 나온다(별도 볼륨 quota를
    걸지 않은 이상). 실패하면(권한 등) None을 반환해 호출부가 그 섹션만 숨기게 한다.
    """
    try:
        usage = disk_usage_fn(path)
    except OSError:
        return None
    gb = 1024 ** 3
    return {
        "total_gb": round(usage.total / gb, 1),
        "used_gb": round(usage.used / gb, 1),
        "free_gb": round(usage.free / gb, 1),
        "used_percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
    }


def get_admin_stats(load_keywords_fn=None, collections: dict | None = None, disk_usage_fn=None) -> dict:
    """관리 화면용 현황 스냅샷.

    load_keywords_fn/collections/disk_usage_fn을 넘기면 그걸 쓴다(테스트용). collections는
    {"memes": ..., "cleaned_memes": ..., "trend_scores": ..., "crawl_requests": ...,
    "hidden_keywords": ..., "llm_requests": ..., "crawl_rejects": ...} 형태 — 일부만
    넘기면 나머지는 실제 Mongo 컬렉션으로 채운다. 다 생략하면 전부 실제 값을 쓴다.
    """
    if load_keywords_fn is None:
        from main import load_keywords as load_keywords_fn
    if disk_usage_fn is None:
        disk_usage_fn = shutil.disk_usage

    collections = dict(collections or {})
    missing = [key for key in _COLLECTION_NAMES if key not in collections]
    if missing:
        from DB.mongo_client import get_collection
        for key in missing:
            collections[key] = get_collection(_COLLECTION_NAMES[key])

    keywords = load_keywords_fn()

    collection_counts = {
        key: collections[key].count_documents({}) for key in _COLLECTION_NAMES
    }

    # promotion_skipped="cap" — 상한에 걸려 Keywords.md 편입이 거부된 키워드들.
    # crawl_requests에 남긴 플래그를 그대로 읽어 관리자에게 "정리하면 새 키워드가 들어갈 수
    # 있다"는 알림으로 보여준다.
    capped_docs = collections["crawl_requests"].find({"promotion_skipped": "cap"}, {"_id": 1})
    capped_keywords = [doc["_id"] for doc in capped_docs]

    return {
        "keyword_count": len(keywords),
        "keyword_cap": MAX_BATCH_KEYWORDS,
        "collection_counts": collection_counts,
        "per_keyword_doc_counts": _count_per_keyword_by_source(collections["memes"], keywords),
        "capped_keywords": capped_keywords,
        "disk_usage": _disk_usage_stats(disk_usage_fn),
    }

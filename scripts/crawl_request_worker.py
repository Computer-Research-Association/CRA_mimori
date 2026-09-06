"""
crawl_request_worker.py
crawl_requests 큐에서 가장 오래된 queued 요청 하나를 집어서 크롤링→정제→임베딩까지
이어서 처리한다. scheduler가 1분마다 이 스크립트를 서브프로세스로 실행한다
(BGE-M3 등 무거운 의존성을 프로세스 종료와 함께 OS가 회수하게 하려고 —
기존 crawlrun/preprocess_embed_run과 동일한 이유).

큐가 비어있으면 아무 일도 하지 않고 조용히 종료한다(다음 틱에 재시도).
동시 처리 1개 제한은 이 스크립트가 아니라 scheduler.py의 APScheduler
max_instances(기본값 1)가 보장한다 — 같은 job이 아직 안 끝났으면 다음 틱을 건너뛴다.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DB.mongo_client import get_collection
from config.config_cilent import (
    CRAWL_REQUESTS_COLLECTION,
    LLM_REQUESTS_COLLECTION,
    MAX_BATCH_KEYWORDS,
    MIN_COMMUNITY_DOCS_FOR_TAVILY,
    ON_DEMAND_MAX_POSTS,
)
from scripts.heavy_job_lock import acquire_heavy_job_lock_blocking, release_heavy_job_lock

# main/preprocessing.pipeline/embedding.pipeline은 torch, FlagEmbedding, 크롤러 6개,
# boto3(CloudWatch 로그스트림 3개)를 끌고 와 임포트만으로 몇 초가 든다. 이 스크립트는
# 큐가 비어있어도(흔한 경우) 1분마다 서브프로세스로 뜨므로, 최상단에서 바로 임포트하면
# 그 비용을 매 틱 문다(실측: 하루 1440회 × 약 8초). 그래서 처리할 큐 항목이 실제로
# 있을 때만 _load_pipeline()에서 지연 로드한다 — None이 그 "아직 안 불렀다" 신호다.
crawl_all = None
add_keyword_if_missing = None
load_keywords = None
preprocess_documents = None
embed_documents = None
# perf_log도 같은 이유로 지연 로드한다 — logging_config를 거쳐 boto3/watchtower를 끌고 온다.
report_totals = None
# trend_service는 torch 등 무거운 의존성이 없어 매 틱 임포트해도 비용이 미미하지만,
# 다른 파이프라인 함수들과 같은 지연 로드 방식으로 통일해둔다 — 그래야 아래 guard(
# "crawl_all이 이미 patch돼 있으면 다시 임포트하지 않는다")가 이 함수들에도 그대로
# 적용되고, 기존 tests/test_crawl_request_worker.py가 crawl_all 등만 monkeypatch해도
# get_meme_trend가 로드되지 않은 채(None) 남아 실제 네트워크/DB를 안 건드리게 된다.
get_meme_trend = None
save_trend_score = None


def _load_pipeline() -> None:
    """crawl_all/preprocess_documents/embed_documents를 지연 임포트해 모듈 전역에 바인딩한다.

    이미 로드됐거나(None이 아님) 테스트가 worker.crawl_all 등을 직접 패치해둔 경우엔
    다시 임포트하지 않는다 — tests/test_crawl_request_worker.py가 이 이름들을 monkeypatch로
    갈아끼우는 패턴을 그대로 지원하기 위함.
    """
    global crawl_all, add_keyword_if_missing, load_keywords, preprocess_documents, embed_documents, report_totals
    global get_meme_trend, save_trend_score
    if crawl_all is not None:
        return
    from main import (
        crawl_all as _crawl_all,
        add_keyword_if_missing as _add_keyword_if_missing,
        load_keywords as _load_keywords,
    )
    from preprocessing.pipeline import preprocess_documents as _preprocess_documents
    from embedding.pipeline import embed_documents as _embed_documents
    from perf_log import report_totals as _report_totals
    from trend.trend_service import (
        get_meme_trend as _get_meme_trend,
        save_trend_score as _save_trend_score,
    )
    crawl_all = _crawl_all
    add_keyword_if_missing = _add_keyword_if_missing
    load_keywords = _load_keywords
    preprocess_documents = _preprocess_documents
    embed_documents = _embed_documents
    report_totals = _report_totals
    get_meme_trend = _get_meme_trend
    save_trend_score = _save_trend_score


def _judge_trend_early(keyword: str) -> None:
    """크롤링(수십 분) 시작 전에 트렌드 판정부터 먼저 끝내서 화면에 z-score를 빨리 보여준다.

    트렌드 API(네이버/카카오/구글)는 크롤링·임베딩과 완전히 독립적이라 먼저 계산할 수
    있다(보통 몇 초). 프론트(CrawlRequestPanel)가 /trend/<keyword>를 폴링하다가 여기서
    저장한 값을 그대로 읽어간다. 부가 기능이라 실패해도 크롤링 자체를 막으면 안 된다
    (get_meme_trend/save_trend_score 미로드 시에도 조용히 스킵 — report_totals와 동일 패턴).
    """
    if get_meme_trend is None:
        return
    try:
        save_trend_score(get_meme_trend(keyword))
    except Exception as e:
        print(f"[워커] 조기 트렌드 판정 실패, 계속 진행: {e}")


def _mark(collection, keyword: str, status: str, error: str | None = None) -> None:
    update = {"status": status, "completed_at": datetime.now(timezone.utc)}
    if error is not None:
        update["error"] = error
    collection.update_one({"_id": keyword}, {"$set": update})


def _set_stage(collection, keyword: str, stage: str) -> None:
    """현재 어느 단계(crawl/preprocess/embed)를 돌고 있는지 문서에 남긴다.

    진행 표시는 부가 정보라 실패해도 작업 자체를 중단시키지 않는다 — 수십 분짜리
    수집을 진행률 갱신 실패 하나로 날리는 쪽이 훨씬 나쁘다.
    """
    try:
        collection.update_one({"_id": keyword}, {"$set": {"stage": stage}})
    except Exception as e:
        print(f"[워커] 단계 기록 실패 ({stage}): {e}")


def _make_progress_callback(collection, keyword: str):
    """크롤 소스 하나가 끝날 때마다 진행 상황을 crawl_requests 문서에 기록하는 콜백.

    사용자가 화면 앞에서 20분 가까이 기다리는 동안 status가 running 하나로만 고정돼
    "멈춘 것처럼" 보이던 문제(= 체감 지연)를 없애기 위함이다. 실제 소요 시간은
    줄지 않지만 어느 소스가 몇 건 끝났는지가 보인다.

    crawl_all의 병렬 풀(CRAWL_WORKERS) 스레드에서 호출되므로, 문서 전체를 읽어
    다시 쓰지 않고 progress.<source> 필드만 $set 한다 — read-modify-write로 짜면
    스레드끼리 서로의 갱신을 덮어쓴다.
    """
    def on_source_done(_keyword: str, source: str, count: int, status: str) -> None:
        try:
            collection.update_one(
                {"_id": keyword},
                {"$set": {f"progress.{source}": {"count": count, "status": status}}},
            )
        except Exception as e:
            print(f"[워커] 진행 상황 기록 실패 ({source}): {e}")

    return on_source_done


def _requeue_stale_running(collection, stale_after: timedelta = timedelta(hours=2)) -> None:
    """max_instances=1이 동시 실행은 막아주지만, 프로세스가 크롤링 도중 죽으면(OOM 등)
    Mongo 문서만 running에 멈춰 남는다. 이 함수는 그런 고아 상태를 다음 실행 때 회수한다."""
    cutoff = datetime.now(timezone.utc) - stale_after
    collection.update_many(
        {"status": "running", "started_at": {"$lt": cutoff}},
        {"$set": {"status": "queued"}},
    )


def run_once(collection=None, llm_requests_collection=None) -> None:
    """큐에서 가장 오래된 queued 요청 하나를 처리. 없으면 즉시 반환."""
    if collection is None:
        collection = get_collection(CRAWL_REQUESTS_COLLECTION)
    if llm_requests_collection is None:
        llm_requests_collection = get_collection(LLM_REQUESTS_COLLECTION)

    _requeue_stale_running(collection)

    # 재시도(failed→queued)나 고아 회수로 다시 도는 경우 이전 실행의 진행 상황이 남아
    # 있으면 안 된다 — 끝난 적 없는 소스가 "완료"로 보인다. 집는 시점에 같이 초기화한다.
    doc = collection.find_one_and_update(
        {"status": "queued"},
        {"$set": {
            "status": "running",
            "started_at": datetime.now(timezone.utc),
            "stage": "crawl",
            "progress": {},
        }},
        sort=[("requested_at", 1)],
    )
    if doc is None:
        return

    keyword = doc["_id"]
    try:
        _load_pipeline()
        _judge_trend_early(keyword)
        crawl_all(
            [keyword],
            on_source_done=_make_progress_callback(collection, keyword),
            max_posts=ON_DEMAND_MAX_POSTS,
        )
        _set_stage(collection, keyword, "preprocess")
        preprocess_documents(keyword)

        # crawl_all/preprocess_documents는 이미 끝낸 sunk cost라, 락을 못 잡아도
        # 포기하지 않고 최대 10분까지 기다린다(짧게 한 번 시도하고 포기하는
        # llm_request_worker와 다른 이유: 거긴 사전 작업이 없어 포기 비용이 0).
        if not acquire_heavy_job_lock_blocking("crawl_request_worker"):
            _mark(collection, keyword, "failed", error="임베딩 락 획득 시간 초과(다른 무거운 작업이 오래 실행 중)")
            return
        try:
            embed_result = embed_documents(keyword)
        finally:
            release_heavy_job_lock("crawl_request_worker")

        if embed_result.get("documents", 0) == 0:
            _mark(collection, keyword, "failed", error="수집된 데이터가 없습니다 (모든 소스에서 관련 자료를 찾지 못했습니다)")
        else:
            # Keywords.md에 넣는 순간 매일 배치 크롤·트렌드 판정 대상이 되고 빼는 경로는
            # 없다. 오타나 일회성 질의가 영구히 Tavily/YouTube 쿼터를 갉아먹지 않도록,
            # 배치에서 커뮤니티 수집이 '충분하다'고 보는 기준(MIN_COMMUNITY_DOCS_FOR_TAVILY)을
            # 넘긴 키워드만 편입한다(issue #69 — rag_main.py의 온디맨드 CLI 경로와 동일 기준).
            if embed_result.get("documents", 0) >= MIN_COMMUNITY_DOCS_FOR_TAVILY:
                if len(load_keywords()) < MAX_BATCH_KEYWORDS:
                    add_keyword_if_missing(keyword)
                else:
                    # 상한 도달 — 이 요청 자체(수집·분석)는 정상 처리다, 사용자는 결과를
                    # 이미 받는다. Keywords.md 편입만 거부하고 관리자 화면이 볼 플래그를
                    # 남긴다. 관리자가 오래된/비인기 키워드를 정리하면 다음 신규 키워드부터
                    # 다시 편입된다.
                    collection.update_one({"_id": keyword}, {"$set": {"promotion_skipped": "cap"}})
            _mark(collection, keyword, "done")
            llm_requests_collection.delete_many({"keyword": keyword, "status": "done"})
    except Exception as e:
        _mark(collection, keyword, "failed", error=str(e))
    finally:
        # 단계별 누적 시간(크롤/임베딩 인코딩/근접중복 필터/Qdrant 등)을 로그로 남긴다.
        # 여태 perf_log가 값을 쌓기만 하고 이 경로에서는 아무도 출력하지 않아,
        # 정작 제일 느린 온디맨드 수집만 계측이 안 보이는 상태였다.
        # 실패한 실행일수록 어디서 시간을 썼는지가 필요하므로 finally에 둔다.
        if report_totals is not None:
            report_totals(f"온디맨드 수집 '{keyword}' 단계별 누적 소요 시간")


if __name__ == "__main__":
    run_once()

# analyze/rag 성능 개선 + 비동기화 설계

## 목적

`/api/analyze`, `/api/rag`가 동기 HTTP 요청 안에서 임베딩(BGE-M3, CPU) + Qdrant 4-facet 순차 검색 + 대형 LLM(openai/gpt-oss-120b, 최대 16384 토큰) 호출을 전부 처리하다 보니 요청 하나가 수 분~10분(nginx/gunicorn 타임아웃 600초)까지 걸린다. 이 작업은 그 지연을 다음 방식으로 줄인다.

1. 같은 keyword/질문을 반복 요청해도 처음부터 다시 계산하지 않도록 **결과 캐싱**
2. 무거운 계산을 API 프로세스에서 떼어내 **scheduler가 큐를 처리**하는 비동기 구조로 전환(이미 `crawl_requests`에서 쓰는 패턴 재사용)
3. facet 4개를 순차가 아니라 **병렬로 검색**
4. LLM 출력 토큰 상한을 16384 → **4096**으로 축소
5. (원래 "임베딩 서비스 분리"로 제안했던 항목) — 2번 재설계로 API 프로세스가 BGE-M3를 아예 안 올리게 되므로 **별도 작업 불필요**. 크롤 워커도 이미 서브프로세스 종료 시 메모리를 회수하는 동일한 트레이드오프를 쓰고 있어 일관성 있음.

## 범위

**포함**: `llm_requests` 큐/캐시 컬렉션, `/api/analyze-request`·`/api/rag-request` 4개 엔드포인트, scheduler의 `llm_request_job`(서브프로세스 워커), 크롤 워커·analyze/rag 워커 간 `heavy_job_lock`, `facet_search` 병렬화, 토큰 상한 축소, 프론트 폴링 UI 전환.

**제외 (의도적으로 미룸)**: GPU 인스턴스 이전, 버스터블→논버스터블 인스턴스 전환(둘 다 순수 인프라/비용 결정이라 코드 변경 범위 밖). rate limit/남용 방지는 기존과 동일하게 미룸.

**기존 동작 변경**: `/api/analyze`, `/api/rag`(동기 엔드포인트)는 제거하고 `/api/analyze-request`(POST/GET), `/api/rag-request`(POST/GET)로 대체한다. 두 엔드포인트를 HTTP로 호출하는 곳은 프론트와 테스트뿐이라(다른 외부 consumer 없음) 하위호환을 유지할 필요가 없다.

## 아키텍처

```
[프론트]
  POST /api/analyze-request {keyword}                  → 202 {keyword, status}
  GET  /api/analyze-request/<keyword>                   → 200 {keyword, status, result, sources, trend, error}
  POST /api/rag-request {keyword, question, sources}     → 202 {job_id, status}
  GET  /api/rag-request/<job_id>                         → 200 {job_id, status, answer, sources, trend, error}
  (CrawlRequestPanel.jsx와 동일한 폴링 패턴, 3~5초 간격)

[api 컨테이너] — 가벼움. Mongo 읽기/쓰기만, 무거운 의존성 import 없음.
  POST: llm_requests에 같은 _id로 status="done" 문서가 있으면 그대로 반환(캐시 히트).
        없으면 status="queued"로 upsert(멱등성, crawl_requests와 동일 원칙).
  GET:  상태/결과 조회만.

[scheduler 컨테이너] — 무거운 작업 전담 (기존 crawl_request_job과 동일 패턴)
  llm_request_job (interval, 5초, max_instances 기본값 1로 자동 직렬화)
    → scripts/llm_request_worker.py를 서브프로세스로 실행
        1. heavy_job_lock 획득 시도 (실패 시 이번 틱 skip, 다음 틱 재시도)
        2. llm_requests에서 가장 오래된 status="queued" 1개를 find_one_and_update로 "running"으로 전환
        3. 없으면 조용히 종료
        4. kind로 분기
           - "analyze": encode_facets → facet_search(병렬) → build_facet_prompt → analyze()
           - "rag":     encode_batch(질문) → search_relevant_chunks → build_rag_prompt → analyze()
        5. 결과를 result 필드에 저장, status="done"
        6. 예외 발생 시 status="failed" + error 저장
        7. finally: heavy_job_lock 해제
        8. 프로세스 종료 → BGE-M3 등 무거운 의존성 메모리를 OS가 회수(crawl_request_worker와 동일 트레이드오프: 매 요청마다 재로드 비용이 들지만 메모리 안전이 우선)
```

## 데이터 모델

### `llm_requests` — analyze/rag 통합 큐 겸 캐시

analyze와 rag를 한 컬렉션으로 합친다. **완료된 문서 자체가 캐시**라서 캐싱과 큐를 별도로 구현할 필요가 없다.

```
{
  _id: str,                    # analyze: keyword 그대로 (crawl_requests와 동일 원칙)
                                # rag: sha256(f"{keyword}|{question}|{','.join(sorted(sources or []))}").hexdigest()
  kind: "analyze" | "rag",
  keyword: str,
  question: str | None,        # rag만
  sources: list[str] | None,   # rag만
  status: "queued" | "running" | "done" | "failed",
  requested_at: datetime,
  started_at: datetime | None,
  completed_at: datetime | None,
  error: str | None,
  result: dict | None,         # analyze: {"result", "sources", "trend"} / rag: {"answer", "sources", "trend"}
}
```

`_id`가 자연키(keyword 또는 질문 해시)라서, 이미 큐에 있거나 처리 중/완료된 동일 요청을 다시 보내면 새 문서를 만들지 않고 기존 상태를 그대로 반환한다(멱등성, `crawl_requests`와 동일).

**캐시 무효화**: 아래 두 시점에 해당 keyword의 `llm_requests` 문서를 전부 `delete_many({"keyword": keyword})`.
- `scripts/crawl_request_worker.py`가 크롤 요청을 `"done"`으로 마크할 때
- `analysis/pipeline.py`의 `delete_keyword_permanently()` 호출 시

무효화된 keyword를 다시 analyze/rag 요청하면 문서가 없으므로 자연스럽게 새로 계산된다.

### `locks` — `heavy_job_lock` 단일 문서

크롤 워커(`embed_documents()` 구간)와 analyze/rag 워커(임베딩~LLM 호출 구간)가 동시에 BGE-M3를 메모리에 올리지 않도록 막는다. **크롤링 자체(네트워크 대기, 수십 분)는 락 밖**이고, 실제로 BGE-M3가 메모리에 있는 구간만 감싼다 — 그래야 크롤이 analyze 요청을 오래 막지 않는다.

```
{ _id: "heavy_job_lock", locked: bool, owner: str, locked_at: datetime | None }
```

```python
STALE_LOCK_TIMEOUT = timedelta(minutes=20)

def acquire_heavy_job_lock(owner: str) -> bool:
    now = datetime.now(timezone.utc)
    collection = get_collection(LOCKS_COLLECTION)
    collection.update_one(
        {"_id": "heavy_job_lock"},
        {"$setOnInsert": {"locked": False, "owner": None, "locked_at": None}},
        upsert=True,
    )  # 락 문서가 없으면 미리 만들어둠 — 이후 락 획득 시도는 항상 순수 update라 upsert 경합이 안 생김
    stale_cutoff = now - STALE_LOCK_TIMEOUT
    result = collection.update_one(
        {
            "_id": "heavy_job_lock",
            "$or": [{"locked": False}, {"locked_at": {"$lt": stale_cutoff}}],
        },
        {"$set": {"locked": True, "owner": owner, "locked_at": now}},
    )
    return result.modified_count == 1

def release_heavy_job_lock(owner: str) -> None:
    get_collection(LOCKS_COLLECTION).update_one(
        {"_id": "heavy_job_lock", "owner": owner},
        {"$set": {"locked": False, "owner": None}},
    )
```

`update_one`의 원자성 덕분에 두 워커가 동시에 시도해도 하나만 `modified_count == 1`을 받는다. `locked_at`이 20분 넘게 지난 락은 소유자가 비정상 종료(OOM 등)한 것으로 보고 다음 시도자가 회수한다.

## API 명세

| 메서드/경로 | 요청 | 응답 | 비고 |
|---|---|---|---|
| `POST /api/analyze-request` | `{"keyword": str}` | `202 {"keyword","status"}` | 이미 `done`이면 즉시 그 status 반환(폴링 없이 GET 한 번으로 결과 확인 가능) |
| `GET /api/analyze-request/<keyword>` | - | `200 {"keyword","status","result","sources","trend","error"}` | 요청 이력 없으면 404 |
| `POST /api/rag-request` | `{"keyword","question","sources"?}` | `202 {"job_id","status"}` | `job_id = sha256(keyword\|question\|sources)` |
| `GET /api/rag-request/<job_id>` | - | `200 {"job_id","status","answer","sources","trend","error"}` | 없으면 404 |

두 POST/GET 쌍 모두 `crawl-request`와 동일하게 `@limited`(동시성 세마포어) 대상이 아니다 — Mongo 문서 읽기/쓰기뿐인 가벼운 작업.

## 컴포넌트별 변경

| 파일 | 변경 |
|---|---|
| [api/routes.py](../../../api/routes.py) | `/analyze`, `/rag` 제거 → `/analyze-request`, `/rag-request` (POST+GET) 추가 |
| [api/app.py](../../../api/app.py) | `MAX_CONCURRENT_REQUESTS`/`limited` 삭제(무거운 작업이 API에서 안 돌므로 불필요) |
| `scripts/llm_request_worker.py` (신규) | `crawl_request_worker.py`와 동일 구조(`_requeue_stale_running` 포함)로 큐 처리, `kind`로 analyze/rag 분기 |
| `scheduler/scheduler.py` | `llm_request_job` 추가: `add_job(llm_request_run, 'interval', seconds=5, id='llm_request_job', coalesce=True, misfire_grace_time=30, replace_existing=True)` |
| [analysis/rag_pipeline.py](../../../analysis/rag_pipeline.py) `facet_search` (461-471행) | 4개 facet 순차 for → `ThreadPoolExecutor(max_workers=4)`로 병렬 검색, 결과는 facet 이름 키의 dict로 취합(순서 무관) |
| [analysis/pipeline.py](../../../analysis/pipeline.py) `analyze()` (180행) | `max_completion_tokens=16384` → `4096` |
| [analysis/pipeline.py](../../../analysis/pipeline.py) `delete_keyword_permanently()` (64행) | `llm_requests` 컬렉션 삭제 추가 |
| `scripts/crawl_request_worker.py` | done 처리 시 `llm_requests` 무효화 추가, `embed_documents()` 호출을 `heavy_job_lock`으로 감쌈 |
| `config/config_cilent.py` | `LLM_REQUESTS_COLLECTION = "llm_requests"`, `LOCKS_COLLECTION = "locks"` 추가 |
| `frontend/src/api.js` | `analyzeKeyword`/`askRag` 제거 → `submitAnalyzeRequest`/`fetchAnalyzeStatus`, `submitRagRequest`/`fetchRagStatus` 추가 |
| `frontend/src/components/AnalysisPanel.jsx`, `RagPanel.jsx` | `CrawlRequestPanel.jsx`(제출 → 폴링 → 완료/실패) 패턴으로 재작성 |

## 에러 처리

- LLM/Qdrant 오류 → `status="failed"` + `error` 저장, 프론트는 "다시 시도" 버튼 표시(크롤 패턴과 동일).
- 워커 프로세스가 처리 도중 죽으면(OOM 등) `running`에 고아로 남은 문서를 `_requeue_stale_running`이 다음 실행 때 `queued`로 되돌림(`crawl_request_worker.py`와 동일 로직, timeout 기준은 analyze/rag가 크롤보다 훨씬 짧으므로 20분 정도로 크롤(2시간)보다 짧게 잡음).
- `heavy_job_lock`이 stale(20분 초과)이면 자동 회수.
- 동일 요청 중복 제출 → 멱등적으로 기존 상태만 반환.

## 테스트

- `llm_request_worker.run_once()`: analyze/rag 분기, 성공/실패 상태 반영(pipeline 함수 mock), 큐 비었을 때 조용히 반환, 고아 `running` 회수.
- `heavy_job_lock`: 정상 획득/해제, 동시 획득 시도 시 하나만 성공, stale 락 회수.
- `facet_search` 병렬화: 병렬 실행 후 병합 결과가 기존 순차 실행과 동일한지(순서 무관성) — 기존 facet_search 테스트가 있으면 그대로 통과해야 함.
- `POST/GET /api/analyze-request`, `/api/rag-request`: 캐시 히트(기존 done 문서 즉시 반환), 신규 큐잉, 이미 큐에 있으면 중복 안 만듦, 없는 job 404.
- 캐시 무효화: 크롤 완료·키워드 삭제 시 해당 keyword의 `llm_requests` 문서가 지워지는지.
- 프론트: `CrawlRequestPanel.test.jsx` 패턴으로 `AnalysisPanel`/`RagPanel` 폴링 동작 테스트(제출 실패, 폴링 중 done/failed 전이).

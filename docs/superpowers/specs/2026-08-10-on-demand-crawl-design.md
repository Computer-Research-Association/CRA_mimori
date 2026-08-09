# 새 키워드 온디맨드 수집 기능 설계 (`crawl_requests`)

## 목적

지금까지 밈 분석/RAG는 `crawlers/Keywords.md`에 미리 등록된 키워드만 다룰 수 있다. 이번 작업은 웹사이트 사용자가 아직 데이터가 없는 새 키워드를 입력했을 때, **명시적 버튼으로 수집을 요청**하면 크롤링→정제→임베딩까지 자동으로 이어져서 얼마 뒤(보통 수십 분) 그 키워드도 분석/질의응답이 가능해지도록 하는 기능이다.

이 기능은 [`api/`](2026-08-07-ec2-web-api-design.md)에서 이미 지킨 원칙("API 서버는 가볍게, 무거운 작업은 scheduler 컨테이너가 전담")을 그대로 따른다 — API는 요청을 큐에 넣기만 하고, 실제 크롤링/임베딩은 scheduler의 새 주기 작업이 처리한다.

## 범위

**포함**: 새 키워드 수집 요청 API 2개, `crawl_requests` Mongo 컬렉션, scheduler의 새 주기 작업(큐 처리 워커).

**제외 (의도적으로 미룸)**: 남용 방지(같은 사람이 무의미한 키워드를 계속 요청하는 것 자체를 막는 rate limit) — 기존 API 남용 방지와 동일하게 나중으로. 데이터 소스(크롤러) 켜고 끄기 기능은 완전히 별개의 후속 작업(관리자 인증이 먼저 필요)이라 이 스펙에 없음.

## 아키텍처

```
[api 컨테이너]
  POST /api/crawl-request      → crawl_requests에 {keyword, status:"queued"} upsert, 202 즉시 응답
  GET  /api/crawl-request/<kw> → 현재 상태 조회 (프론트가 폴링)

[scheduler 컨테이너] — 기존 scheduler.py에 새 주기 작업 추가
  crawl_request_job (1분마다)
    → scripts_entry: crawl_request_worker.py를 서브프로세스로 실행
        (기존 crawlrun/preprocess_embed_run과 동일 패턴 — BGE-M3
         메모리를 프로세스 종료와 함께 OS가 회수하게 하기 위함)
    → crawl_request_worker.py:
        1. crawl_requests에서 status="queued" 중 가장 오래된 것 1개 조회
        2. 없으면 즉시 종료(다음 틱에 재시도)
        3. 있으면 status="running"으로 변경
        4. crawl_all([keyword])          # main.py, 기존 함수 그대로 재사용
        5. preprocess_documents(keyword) # preprocessing/pipeline.py, 기존 함수 그대로 재사용
        6. embed_documents(keyword)      # embedding/pipeline.py, 기존 함수 그대로 재사용
        7. 전부 성공 → status="done". 어느 단계든 예외 → status="failed", error 필드에 메시지 저장
```

**동시 처리 1개 제한**: 새 로직을 따로 만들지 않는다. APScheduler는 같은 job id의 이전 실행이 안 끝났으면 다음 틱을 기본적으로 건너뛴다(`max_instances` 기본값 1) — `crawl_request_job`을 1분 주기로 등록해두면, 한 키워드 처리가 1분보다 오래 걸려도 자동으로 다음 키워드는 그 다음 틱까지 대기한다. 별도의 락 코드가 필요 없다.

## 데이터 모델 — `crawl_requests` 컬렉션

```
{
  _id: keyword(문자열, 그 자체를 id로 씀 — 같은 키워드 중복 요청 방지에 자연스럽게 씀),
  status: "queued" | "running" | "done" | "failed",
  requested_at: datetime,
  started_at: datetime | null,
  completed_at: datetime | null,
  error: str | null,
}
```

`_id`가 keyword 자체이므로, 이미 큐에 있거나 처리 중/완료된 키워드를 다시 요청하면 **새 문서를 만들지 않고 기존 상태를 그대로 반환**한다(멱등성).

## API 명세

| 메서드/경로 | 요청 | 응답 | 비고 |
|---|---|---|---|
| `POST /api/crawl-request` | `{"keyword": str}` | `202 {"keyword","status"}` | 이미 `/api/keywords`에 있는 키워드면 `400 {"error": "이미 존재하는 키워드입니다"}` |
| `GET /api/crawl-request/<keyword>` | - | `200 {"keyword","status","requested_at","completed_at","error"}` | 요청 이력 없으면 `404` |

이 두 엔드포인트는 동시성 제한(`@limited`) 대상이 아니다 — Mongo에 문서 하나 읽고 쓰는 가벼운 작업이라 `/api/analyze`/`/api/rag`와 성격이 다르다.

## 컴포넌트 상세

### 신규 파일

```
scripts/crawl_request_worker.py   # 서브프로세스 진입점 (scheduler가 호출)
```

```python
def run_once() -> None:
    """큐에서 가장 오래된 queued 요청 하나를 처리. 없으면 즉시 반환."""
    doc = get_collection(CRAWL_REQUESTS_COLLECTION).find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
        sort=[("requested_at", 1)],
    )
    if doc is None:
        return
    keyword = doc["_id"]
    try:
        crawl_all([keyword])
        preprocess_documents(keyword)
        embed_documents(keyword)
        _mark(keyword, "done")
    except Exception as e:
        _mark(keyword, "failed", error=str(e))
```

`find_one_and_update`로 조회+상태변경을 원자적으로 처리 — 여러 워커가 동시에 뜨는 극단적 상황에서도 같은 키워드를 두 번 집지 않는다(다만 위에서 설명한 APScheduler `max_instances=1`이 이미 이 상황 자체를 막아주므로 이중 안전장치 성격).

### `api/routes.py`에 추가

```python
@bp.route("/crawl-request", methods=["POST"])
def crawl_request_endpoint():
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"error": "keyword가 필요합니다"}), 400
    if keyword in list_analyzable_keywords():
        return jsonify({"error": "이미 존재하는 키워드입니다"}), 400

    existing = get_collection(CRAWL_REQUESTS_COLLECTION).find_one({"_id": keyword})
    if existing:
        return jsonify({"keyword": keyword, "status": existing["status"]}), 202

    get_collection(CRAWL_REQUESTS_COLLECTION).insert_one({
        "_id": keyword, "status": "queued",
        "requested_at": datetime.now(timezone.utc),
        "started_at": None, "completed_at": None, "error": None,
    })
    return jsonify({"keyword": keyword, "status": "queued"}), 202


@bp.route("/crawl-request/<keyword>")
def crawl_request_status(keyword):
    doc = get_collection(CRAWL_REQUESTS_COLLECTION).find_one({"_id": keyword})
    if doc is None:
        return jsonify({"error": "요청 이력이 없습니다"}), 404
    return jsonify({
        "keyword": doc["_id"], "status": doc["status"],
        "requested_at": doc["requested_at"].isoformat(),
        "completed_at": doc["completed_at"].isoformat() if doc["completed_at"] else None,
        "error": doc["error"],
    })
```

### `scheduler/scheduler.py`에 추가

```python
scheduler.add_job(
    crawl_request_run,   # subprocess.run([sys.executable, CRAWL_REQUEST_WORKER_PY]) 래퍼, 기존 crawlrun 패턴과 동일
    'interval',
    minutes=1,
    id='crawl_request_job',
    coalesce=True,
    misfire_grace_time=60,
    replace_existing=True,
)
```

### `config/config_cilent.py`에 추가

```python
CRAWL_REQUESTS_COLLECTION = "crawl_requests"
```

## 에러 처리

- 크롤링/정제/임베딩 중 어느 단계든 예외가 나면 `status="failed"` + `error` 필드에 메시지 저장. 프론트는 이 상태를 보고 "수집에 실패했습니다"를 표시.
- `failed` 상태는 재요청 가능하게 한다 — `POST /api/crawl-request`가 기존 문서를 그대로 반환하는 로직에 예외를 둬서, `status == "failed"`인 기존 문서는 `queued`로 리셋하고 새로 시도하게 함(스펙에 명시: 멱등성 규칙의 유일한 예외).

## 테스트

- `crawl_request_worker.run_once()`: 큐에 아무것도 없을 때 조용히 반환하는지, 정상 처리 시 done으로 바뀌는지, 예외 발생 시 failed+error가 기록되는지 — `crawl_all`/`preprocess_documents`/`embed_documents`를 mock해서 오케스트레이션 로직만 검증(실제 크롤링/DB 불필요).
- `POST /api/crawl-request`: 이미 있는 키워드 거부(400), 신규 큐 등록(202), 이미 큐에 있으면 새로 안 만들고 기존 상태 반환, failed 상태는 재요청 시 queued로 리셋.
- `GET /api/crawl-request/<keyword>`: 있으면 200, 없으면 404.

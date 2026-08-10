# EC2 웹 API 서버 설계 (`api/`)

## 목적

지금 EC2(`100.29.36.216`)는 크롤링→전처리→임베딩 파이프라인을 `scheduler/scheduler.py`(크론)로 상시 자동 실행하지만, 외부에서 HTTP로 접근 가능한 통로가 전혀 없다. `docker-compose.yml`의 `flask` 서비스는 이름만 남았고 실제로 실행하는 건 스케줄러이며, 코드베이스 어디에도 `Flask(...)`/`@app.route` 같은 실제 웹 앱이 없다.

이번 작업은 기존 RAG/분석/트렌드 파이프라인을 건드리지 않고 그대로 재사용하면서, 웹사이트가 호출할 수 있는 **HTTP API 서버**를 새로 추가하는 것이다.

## 범위

**포함**: RAG 질의응답, 밈 분석, 트렌드 조회 3개 기능을 HTTP API로 노출. 동시성 상한(자체 인프라 보호용). CORS 전체 허용. `docker-compose.yml`에 `scheduler`/`api` 서비스 분리.

**제외 (의도적으로 미룸)**: 도메인/HTTPS, rate limit·API 키 등 사용자 단위 남용 방지, 프론트엔드(웹사이트) 자체. 이 결정들은 브레인스토밍 대화에서 사용자가 명시적으로 "나중에"로 정함.

## 아키텍처

기존 `docker-compose.yml`은 `flask`(실제로는 스케줄러) / `mongo` / `qdrant` 3개 서비스다. 이를 4개로 바꾼다:

```
services:
  scheduler   # 기존 flask 서비스 이름 정리. CMD는 그대로 scheduler/scheduler.py
  api         # 신규. gunicorn으로 Flask 앱 실행, 포트 노출
  mongo       # 변경 없음
  qdrant      # 변경 없음
```

두 서비스로 나누는 이유: `scheduler/scheduler.py`가 이미 크롤/임베딩을 별도 서브프로세스로 격리하는 것과 같은 원칙 — API 서버가 트래픽 폭주나 메모리 문제로 죽어도 매일 도는 데이터 수집(크론)에는 영향이 없어야 한다. 같은 `Dockerfile`을 재사용하고 `command:`만 서비스별로 다르게 override한다(Dockerfile을 2개로 늘리지 않음).

```
Dockerfile (기존 그대로, CMD는 scheduler 기본값 유지)
docker-compose.yml
  scheduler:  command 생략(Dockerfile 기본 CMD 사용)
  api:        command: gunicorn -w 1 --threads 4 --timeout 300 -b 0.0.0.0:5000 api.app:app
```

## API 명세

| 메서드/경로 | 요청 body | 응답 (200) | 실패 응답 |
|---|---|---|---|
| `GET /api/health` | - | `{"status": "ok"}` | - |
| `GET /api/keywords` | - | `{"keywords": ["야르", "쌰갈", ...]}` | - |
| `POST /api/analyze` | `{"keyword": str}` | `{"result": str, "trend": dict\|null}` | 400 키워드 누락 / 404 데이터 없음 / 429 동시성 초과 / 500 LLM 실패 |
| `POST /api/rag` | `{"keyword": str, "question": str}` | `{"answer": str, "sources": [{"title","url"}], "trend": dict\|null}` | 400 / 404 / 429 / 500 (위와 동일 사유) |
| `GET /api/trend/<keyword>` | - | `{"keyword","status","final_z","sources",...}` (get_meme_trend 반환 형태 그대로) | 404 캐시된 판정 없음 |

에러 응답은 전부 `{"error": "사람이 읽을 메시지"}` 형태로 통일한다.

## 컴포넌트 상세

```
api/
  app.py        # Flask app factory, CORS 설정, 세마포어, 에러 핸들러, 서버 시작 시 BGE-M3 1회 로딩
  routes.py     # 5개 엔드포인트 (health/keywords/analyze/rag/trend)
```

기존 `analysis/pipeline.py`, `analysis/rag_pipeline.py`, `trend/trend_service.py`, `embedding/encoder.py`는 수정 없이 재사용. 다음 2가지만 신규/수정이 필요하다.

### 1. 트렌드 캐시 읽기 함수 (신규, `trend/trend_service.py`에 추가)

```python
def get_cached_trend(keyword: str) -> dict | None:
    """trend_scores에서 keyword의 가장 최근 날짜 판정 문서를 읽어 반환.
    없으면 None. 실시간으로 naver/kakao/google을 호출하지 않는다
    (rate limit/IP 차단 위험 회피 — 크론이 2시간마다 이미 채워둔 값을 재사용)."""
```

`GET /api/trend/<keyword>`가 이 함수를 직접 쓴다.

### 2. `build_prompt`/`build_rag_prompt`가 실시간 트렌드 호출을 하지 않도록 캐시 결과 전달

**중요 발견**: `analysis/pipeline.py`의 `build_prompt()`와 `analysis/rag_pipeline.py`의 `build_rag_prompt()`는 내부에서 `trend_service.format_trend_context(keyword)`를 인자 없이 호출하는데, `format_trend_context`는 `result=None`이면 **직접 `get_meme_trend()`를 실시간 호출**한다(`trend_service.py:287-292`). 즉 `/api/trend`만 캐시로 막아도, `/api/analyze`·`/api/rag`가 프롬프트를 만들 때 뒷문으로 실시간 트렌드 호출이 새 나간다.

대응: `api/routes.py`에서 `get_cached_trend(keyword)`로 먼저 캐시를 읽고, `format_trend_context(keyword, result=cached)`처럼 **명시적으로 결과를 넘겨** 호출한다. `build_prompt`/`build_rag_prompt` 자체는 이미 `trend_info` 문자열을 받는 시그니처가 아니라 내부에서 `format_trend_context`를 호출하는 구조이므로, 이 두 함수에 `trend_info: str | None = None` 파라미터를 추가해 넘기면 없이 넘어오면 내부 호출(→ 실시간)로 폴백하는 기존 동작과 호환된다. (`rag_main.py`가 `format_trend_for_rag`로 "한 번만 조회해서 재사용"하는 것과 같은 패턴을 캐시 버전으로 API에 적용.)

### 3. 동시성 상한 (`api/app.py`)

```python
_semaphore = threading.Semaphore(2)  # 동시 처리 최대 2개

def limited(view_func):
    def wrapper(*args, **kwargs):
        if not _semaphore.acquire(blocking=False):
            return jsonify({"error": "서버가 바쁩니다. 잠시 후 다시 시도하세요."}), 429
        try:
            return view_func(*args, **kwargs)
        finally:
            _semaphore.release()
    return wrapper
```

`/api/analyze`, `/api/rag`에만 적용 (`/api/health`, `/api/keywords`, `/api/trend`는 가벼워서 제외).

### 4. BGE-M3 모델 상주

`embedding/encoder.py`의 인코더를 **gunicorn worker 시작 시 1회만** 로드하고 요청마다 재사용한다. 기존 CLI(`rag_main.py`)가 요청마다 `encode_batch()` 후 `unload_model()`로 VRAM을 비우던 것과 달리, API는 LLM이 원격(NVIDIA NIM)이라 VRAM을 비울 필요가 없으므로 `unload_model()`을 호출하지 않는다(호출하면 다음 요청에서 콜드 리로드 비용 발생).

## 데이터 흐름 (예: `/api/analyze`)

```
POST /api/analyze {keyword:"야르"}
  → 세마포어 획득 (꽉 차면 429)
  → analysis.pipeline.fetch_keyword_chunks(keyword)         # Qdrant scroll
      비어있으면 → 404
  → trend.trend_service.get_cached_trend(keyword)            # trend_scores read (신규)
  → analysis.pipeline.build_prompt(keyword, chunks, trend_info=...)  # 프롬프트 조립 (수정)
  → analysis.pipeline.analyze(prompt)                        # NVIDIA NIM 호출, 최대 3회 재시도
  → 세마포어 반환
  → {"result": ..., "trend": ...} 응답
```

`/api/rag`는 ②가 `search_relevant_chunks`(질문 임베딩 + 하이브리드 검색)로 바뀌는 것 외 동일.

## 배포 변경

- `docker-compose.yml`: `flask` → `scheduler`로 이름 정리, `api` 서비스 신규 추가(포트 노출, 같은 이미지, command override).
- `pyproject.toml`: `gunicorn`, `flask-cors` 의존성 추가.
- **배포 시 주의**: `- /app/.venv` 익명 볼륨이 컨테이너 재생성 시에도 유지되므로, 의존성을 새로 추가한 뒤 배포할 때는 `docker compose up -d --build -V`(익명 볼륨 갱신)를 써야 한다. 그냥 `--build`만 하면 새 이미지 위에 옛 `.venv`가 덮어써져 `gunicorn: not found` 에러가 날 수 있다.
- AWS 보안그룹: API 포트(예: 5000)만 인바운드 허용 추가. mongo(27017)/qdrant(6333/16333) 포트는 계속 막힌 상태 유지 — 이번 변경으로 실수로 같이 열리지 않는지 확인.

## 에러 처리 원칙

- 입력 검증 실패(키워드/질문 누락) → 400
- 파이프라인 조회 결과 없음(청크 없음, 검색 결과 없음) → 404
- 동시성 초과 → 429
- LLM 호출 등 예외 → 500, 상세 에러는 로그에만 남기고 응답에는 노출하지 않음
- 모든 에러 응답은 `{"error": str}` 형태로 통일

## 테스트

기존 `tests/`처럼 네트워크/DB 없이 순수 로직만 테스트하기 어려운 영역이 많다(엔드포인트가 곧 외부 I/O 오케스트레이션). 우선순위:
- 세마포어 동작(동시성 초과 시 429) — mock으로 순수 유닛 테스트 가능
- 에러 응답 포맷(400/404/429) — 파이프라인 함수를 mock해서 라우트 로직만 테스트
- `get_cached_trend` — DB mock으로 "캐시 있음/없음" 분기 테스트
- 실제 엔드투엔드(진짜 Qdrant/NIM 호출)는 로컬 docker-compose로 수동 curl 확인

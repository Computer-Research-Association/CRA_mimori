# mimori (memeory) 프로젝트 학습 노트

> 팀 공유용/AI 가이드용 파일이 아님. 본인이 이 코드베이스를 톱다운으로 이해하기 위한 개인 학습 메모.
> 2026-07-27 기준으로 작성. 코드가 바뀌면 이 문서는 낡아짐 — 정기적으로 다시 훑어볼 것.

## 1. 한 줄 요약

한국 인터넷 밈/은어(예: "야르")를 여러 커뮤니티에서 크롤링 → 정제/청킹 → 임베딩(Qdrant) →
LLM으로 "이 밈이 무슨 뜻이고 어떤 뉘앙스로 쓰이는지" 분석하거나, 사용자가 자유 질문하면
RAG로 답변해주는 파이프라인. 검색 트렌드(네이버 데이터랩)로 "지금 유행 중/감소/소멸" 상태도 같이 판단.

## 2. 전체 파이프라인 (top-down)

```
[1] 크롤링 (main.py)
    tavily / youtube / namuwiki / natepann(네이트판) / dcinside(디시)
        └─ 각 크롤러가 직접 MongoDB "memes" 컬렉션에 저장
             │
             ▼
[2] 전처리/청킹 (preprocess_main.py → preprocessing/pipeline.py)
    memes(is_embedded=False) 읽음 → 텍스트 정제 + 청크 분할
        └─ MongoDB "cleaned_memes" 컬렉션에 upsert (원본과 같은 _id)
             │
             ▼
[3] 임베딩 (embed_main.py → embedding/pipeline.py)
    cleaned_memes의 청크들을 BGE-M3로 인코딩 (dense + sparse)
        └─ Qdrant "mimori_chunks" 컬렉션에 upsert
        └─ 성공하면 memes.is_embedded = True로 마킹
             │
             ├─────────────────────────────┐
             ▼                             ▼
[4] 분석 (analyze_main.py)           [5] RAG Q&A (rag_main.py)
    키워드 전체 청크를 다 가져와서        질문을 임베딩 → Qdrant 하이브리드
    LLM에게 "무슨 뜻/뉘앙스/유래"        검색(RRF) → top_k 청크로 답변 생성
    분석 요청
             │                             │
             └──────────────┬──────────────┘
                            ▼
                  둘 다 trend/trend_service.py의
                  네이버 데이터랩 검색량 z-score를
                  프롬프트에 {trend_info}로 주입
                  (크롤링 데이터는 트렌드 판단에 안 씀 — 콜드스타트/노이즈 때문)
```

핵심 포인트: **크롤링 → 전처리 → 임베딩은 완전히 분리된 배치 스텝**이고, 각 단계는
Mongo의 `is_embedded` 플래그(또는 `cleaned_memes` 존재 여부)로 "누가 아직 안 처리됐는지"를
스스로 찾아서 처리한다. 하나의 거대한 파이프라인 함수가 아니라 CLI 스크립트 4개
(`main.py`, `preprocess_main.py`, `embed_main.py`, `analyze_main.py`/`rag_main.py`)로 나뉘어 있고
각각 독립 실행 가능.

## 3. 배포 구조 — 로컬 vs EC2 (중요, 헷갈리기 쉬움)

- **로컬 (이 PC)**: `docker-compose.yml`로 flask/mongo/qdrant 3개 컨테이너 실행. 개발/테스트용.
  실제로는 스케줄러가 한 번도 정상 발화한 적이 없어서 최근까지 완전히 비어 있었음.
- **EC2 서버 (100.29.36.216)**: 같은 스택이 상시로 돌면서 실제 데이터가 쌓임
  (mongo `memes` 1383개, `cleaned_memes` 657개 — 2026-07-27 확인 기준). **진짜 데이터는 여기 있음.**
- 로컬 `.env`의 `MONGODB_URI=mongodb://mongo:27017` / `QDRANT_HOST=qdrant`는 docker-compose 내부
  네트워크 이름이라 **EC2로 연결될 일이 절대 없음** — 로컬 작업이 EC2 데이터를 건드릴 위험은 없음.
- EC2 데이터를 로컬 도구(DataGrip 등)로 들여다보고 싶으면 SSH 로컬 포트포워딩 필요:
  ```
  ssh -i ~/.ssh/id_ed25519 -L 27017:localhost:27017 -L 6333:localhost:6333 ec2-user@100.29.36.216
  ```
  단, 이 터널이 떠 있는 동안은 Windows에서 `127.0.0.1:27017`이 (더 구체적인 바인딩이라)
  로컬 docker mongo보다 우선순위를 가져가서, 로컬 도구가 자기도 모르게 EC2 쪽으로 붙을 수 있음.
  확인 끝나면 터널은 닫아두는 게 안전.

## 4. 단계별 상세

### 4.1 크롤링 (`crawlers/`)

- 공통 시그니처: `crawl_<source>(keyword) -> list[dict]`. 각 크롤러가 직접 `get_collection()`으로
  Mongo에 `insert_one` (중복은 `_id = md5(keyword::url)`로 자연 방지, 실패하면 스킵 카운트).
- 저장 필드는 5개 소스 전부 동일: `_id, keyword, source, url, title, content, score, published_date, crawled_at, is_embedded`.
- `crawlers/base.py`: User-Agent 랜덤화, 랜덤 딜레이(패턴 탐지 회피), 차단 감지(`is_blocked`),
  재시도(`safe_get`) 등 공통 유틸.
- 소스별 특이사항:
  - **dcinside(디시)**: 댓글 가져오려면 게시글 페이지에서 `e_s_n_o` 토큰을 먼저 긁어야 함.
  - **natepann(네이트판)**: 기본 정렬 HD(인기순) — 커뮤니티 검증된 글 우선.
  - **tavily**: 스크래핑 없이 API 직접 호출, 5개 중 가장 단순 (페이지네이션/날짜필터 없음).
  - **youtube**: 의도적으로 **날짜 필터 없음** — 밈 유행 당시 영상이 제일 좋은 자료라서.
    대신 댓글 5개 미만이면 제외.
  - **namuwiki**: 검색 없이 URL 직접 접근, 댓글 없음, `<a id="s-1">` 앵커 기준으로 본문 추출
    (CSS 클래스명이 배포마다 바뀌어서 안정적인 앵커를 씀).
- **밈은 수명주기가 있다는 전제**로 (namuwiki 제외) 3년(`CRAWL_MAX_AGE_YEARS`) 넘은 글은 제외.

### 4.2 전처리 (`preprocessing/`)

- `preprocess_documents(keyword=None)`: `memes`에서 `is_embedded: False`인 문서를 정제+청킹해서
  `cleaned_memes`에 upsert (원본과 같은 `_id`).
- `cleaned_memes` 문서 구조:
  ```
  {
    _id, keyword, source, url, title,
    content,        # 원본
    clean_content,  # 정제본
    chunks: [{parent_id, chunk_index, text, source, keyword, url, title,
              section_title, published_date, crawled_at}, ...],
    chunk_count, processed_at
  }
  ```
- 청킹 전략: namuwiki는 섹션 마커(`[[SECTION]] `) 기준 구조 기반 분할, 나머지는 재귀 분할이지만
  `"[댓글]"` 경계는 항상 분리(본문과 댓글이 한 청크에 섞이지 않도록).
- 반복 자음/모음(ㅋㅋㅋㅋㅋ 등)은 **완전 삭제가 아니라 3개로 축약만** — 밈 특유의 어감을
  보존하기 위한 의도적 선택.
- `preprocessing/parent_lookup.py` (`get_parent`/`get_parents`): **현재 아무도 안 씀** — Qdrant
  검색 결과에서 원본 전체 문서를 다시 찾아오는 기능인데 아직 RAG 흐름에 연결 안 됨 (죽은 코드,
  나중에 "출처 원문 보기" 기능 붙일 때 쓰려던 것으로 보임).

### 4.3 임베딩 (`embedding/`)

- BGE-M3로 dense(1024차원) + sparse(lexical weight) 벡터 둘 다 생성.
- Qdrant point id = `uuid5(NAMESPACE_DNS, f"{parent_id}::{chunk_index}")` — Qdrant는 정수/UUID만
  id로 받아서 이렇게 우회. 결정론적이라 재실행해도 덮어쓰기만 됨(중복 안 생김).
- payload: `parent_id, chunk_index, text, source, keyword, url, title, section_title, published_date, crawled_at`.
- 진행 상태 추적은 청크 단위가 아니라 **문서 단위** — 한 문서의 청크 전부 upsert 성공해야
  `memes.is_embedded=True`. 중간에 실패하면 플래그 그대로 두고 다음 실행 때 전체 재시도
  (부분 재개 없음).

### 4.4 분석 (`analyze_main.py` → `analysis/pipeline.py`)

- 키워드 하나의 **모든 청크**를 Qdrant에서 다 긁어와서(벡터 검색 아님, `scroll`) LLM에 통째로 넣고
  "이 밈이 언제/어떤 맥락에서 쓰이는지, 뉘앙스, 원래 뜻, 뜻이 어떻게 변했는지"를 물어봄
  (`analysis/prompt_template.md`).
- **주의**: `config.ANALYSIS_MODEL = "qwen3:8b"`, `analyze_main.py` 모듈 docstring도 "로컬 LLM(Ollama)"라고
  되어 있지만, 실제 `analyze()` 함수는 이 설정을 **완전히 무시**하고 `ChatNVIDIA(model="deepseek-ai/deepseek-v4-flash")`를
  하드코딩 호출함 (NVIDIA NIM 클라우드 API, `NIM_KEY` 사용). 코드 안에 `"""로컬 말고 nvidia api 호출"""`
  주석이 있어서 의도적 전환이었던 것으로 보이는데, 설정값/독스트링/`ollama` import가 안 지워지고 남음.
  → **실제로 어떤 모델이 쓰이는지 확인하려면 config가 아니라 `analysis/pipeline.py`의 `analyze()` 본문을 봐야 함.**

### 4.5 RAG (`rag_main.py` → `analysis/rag_pipeline.py`)

1. 질문 입력 → BGE-M3로 인코딩 → 즉시 `unload_model()`로 VRAM 반환 (Ollama가 GPU 쓸 수 있게).
2. Qdrant 하이브리드 검색: dense/sparse 각각 `limit=top_k*2`로 Prefetch 후 RRF(Reciprocal Rank
   Fusion)로 합쳐 최종 `top_k=5`(`RAG_TOP_K`)개 반환. 키워드로 payload 필터링됨.
3. 검색된 청크 + trend 정보로 프롬프트 구성(`rag_prompt_template.md`) → 같은 `analyze()`(NVIDIA)로 답변 생성.
4. 답변과 함께 "근거 출처"(title/url) 목록 출력.
5. `parent_lookup.py`는 여기서도 안 쓰임 — payload 자체에 title/url/text가 이미 있어서 굳이 원본
   재조회 안 함.

### 4.6 트렌드 (`trend/`)

- "트렌드" = **네이버 데이터랩 검색량 기반 유행도**, 크롤링 데이터 기반이 아님(의도적 배제,
  콜드스타트/노이즈 문제 때문 — `trend_service.py:6-8` 주석).
- `get_meme_trend`: 키워드 + 기본 변형(`"{키워드} 뜻"`, `"{키워드}가 뭐야"`)의 최근 검색 비율을
  가져와서 **robust z-score**(평균/표준편차 대신 median/IQR 사용 — 급상승 스파이크에 덜
  민감하게 하려고)로 계산 → `핫함(z>2) / 유행 중(z≥0) / 감소(z≥-2) / 소멸(z<-2)` 분류.
- `format_trend_context()`가 분석/RAG 프롬프트에 `{trend_info}`로 주입됨. 데이터 없으면 빈 문자열
  반환하도록 명시적으로 처리(안 그러면 "데이터 없음"이 "유행 중"으로 잘못 표시될 뻔했음).
- `main.py`가 `get_meme_trend`를 import는 하는데 **실제로 호출은 안 함** — 죽은 import.

### 4.7 스케줄러 (`scheduler/scheduler.py`)

- APScheduler로 매일 KST 14:00에 `main.py`를 서브프로세스로 실행 + 10분마다 heartbeat 로그.
- 로컬 PC는 상시 켜져있지 않아서 14:00 슬롯을 계속 놓쳐왔음 → 오늘 세션에서
  "시작 시 마지막 크롤링이 KST 14:00 슬롯을 놓쳤으면 즉시 1회 캐치업 실행" 로직 추가함
  (`needs_catchup()`, `last_crawl_at.txt`에 마지막 성공 시각 기록).

## 5. 디렉토리 맵

| 경로 | 역할 |
|---|---|
| `main.py` | 크롤링 오케스트레이터 (진입점) |
| `preprocess_main.py` | 전처리/청킹 CLI (진입점, 대화형) |
| `embed_main.py` | 임베딩 CLI (진입점, 대화형) |
| `analyze_main.py` | 키워드 분석 CLI (진입점, 대화형) |
| `rag_main.py` | RAG Q&A CLI (진입점, 대화형) |
| `crawlers/` | 소스별 크롤러 + `base.py`(공통 유틸) + `Keywords.md`(대상 키워드 목록) |
| `DB/mongo_client.py` | Mongo 연결 싱글턴 (`get_db`/`get_collection`) |
| `preprocessing/` | `cleaner.py`(정제), `chunker.py`(청킹), `pipeline.py`(오케스트레이션), `parent_lookup.py`(현재 미사용) |
| `embedding/` | `encoder.py`(BGE-M3 래퍼), `pipeline.py`(Qdrant upsert) |
| `analysis/` | `pipeline.py`(분석), `rag_pipeline.py`(RAG 검색+프롬프트), `*.md`(프롬프트 템플릿), `langchain_playground.ipynb`(실험 노트북) |
| `trend/` | `trend_service.py`, `datalab_client.py`(네이버 API), `zscore.py` |
| `scheduler/` | 크론 스케줄러 (컨테이너의 실제 진입점, docker-compose상 이름은 `flask`지만 Flask 앱 아님) |
| `config/config_cilent.py` | 전역 설정 (오타 남아있는 파일명 그대로 씀 — `config_client`가 아니라 `config_cilent`) |

## 6. 알아두면 좋은 기술부채 / 헷갈리는 점

- **`flask` 컨테이너/의존성은 이름만 남음** — 실제 Flask 앱(`Flask(...)`, `@app.route`)이 코드베이스
  어디에도 없음. docker-compose의 `flask` 서비스가 실제로 실행하는 건 `scheduler/scheduler.py`.
  나중에 HTTP API를 만들려던 계획의 흔적으로 보임.
- **`qwen3:8b`/Ollama 설정은 죽은 설정** — 실제 LLM 호출은 NVIDIA NIM(`ChatNVIDIA`, deepseek-v4-flash)
  하드코딩. `langchain-ollama`, `ollama` 패키지도 의존성엔 있지만 실제 호출 코드 없음.
- **`preprocessing/parent_lookup.py` 전체가 미사용** — "검색 결과의 원본 문서 보기" 기능용으로
  보이는데 아직 어디서도 안 부름.
- **`main.py`의 `get_meme_trend` import가 미사용** — 트렌드는 분석/RAG 단계에서만 실제로 쓰임.
- **`config_cilent.py`** 파일명 자체가 오타(client→cilent)지만 이미 전역에서 이렇게 import되고
  있어서 그냥 그대로 씀.
- **로컬 mongo/qdrant는 EC2와 별개** — 로컬에서 뭘 하든 진짜 운영 데이터(EC2)엔 영향 없음. 반대로
  로컬에서 "데이터가 없네?"라고 착각하기 쉬움 (실제로 이번에 그랬음).

## 7. 앞으로 볼 때 참고

- 최근 브랜치(`feature/langchain`)는 `analysis/langchain_playground.ipynb`에서 RAG 검색 전략(dense
  단독 vs sparse 단독 vs RRF 융합) 비교 + 프롬프트/모델 파라미터 실험 중 — 아직 `rag_main.py`에
  정식으로 반영되진 않은 실험 단계.
- git 로그의 `feature/auto_crawl_in_server` 머지 이력이 EC2 상시 서버 운영 방향과 일치함.

# mimori

한국 밈/신조어(neologism) 트렌드 분석 파이프라인. 키워드 목록을 대상으로 한국 웹 소스를 크롤링하고,
검색·언급량 기반으로 트렌드 상태를 판정한 뒤, 크롤링한 본문을 근거로 RAG(검색 증강 생성) Q&A를
수행합니다.

## 파이프라인 구조

```
crawl → preprocess(정제+청킹) → embed → analyze / RAG-QA
main.py   preprocess_main.py    embed_main.py   analyze_main.py / rag_main.py
```

데이터는 두 저장소를 거칩니다: **MongoDB**(원본/정제 문서, 트렌드 점수)와 **Qdrant**(dense+sparse
청크 벡터, 하이브리드 검색용).

## 요구 사항

- Python 3.11 (3.12 미지원 — `pyproject.toml`의 `requires-python` 제약)
- [uv](https://docs.astral.sh/uv/) (의존성 관리 공식 경로)
- Docker / Docker Compose (전체 스택 실행 시)

## 로컬 실행

```bash
# 1. 의존성 설치
uv sync

# 2. 환경 변수 설정
cp .env.example .env
# .env를 열어 실제 API 키/DB 접속 정보를 채운다 (아래 "환경 변수" 참고)

# 3. MongoDB / Qdrant 실행 (Docker Compose 사용 시)
docker-compose up -d mongo qdrant

# 4. 파이프라인 단계별 실행 (순서대로)
uv run python main.py              # 크롤링 + 트렌드 판정
uv run python preprocess_main.py   # 정제 + 청킹
uv run python embed_main.py        # 임베딩 + Qdrant 적재
uv run python analyze_main.py      # 키워드 단위 분석
uv run python rag_main.py          # 키워드 + 질문 → RAG 답변

# 검색 방식(Dense/Sparse/Hybrid) 비교 평가
uv run python -m eval.run_compare
```

각 스크립트는 저장소 루트를 `sys.path`에 넣고 직접 실행되며, 대부분 대화형 stdin 입력(키워드 선택,
질문 등)을 받습니다.

## Docker로 전체 스택 실행

```bash
docker-compose up --build
```

- 스케줄러 컨테이너는 매일 KST 14:00에 `main.py`를 자동 실행합니다(놓친 시간대는 캐치업 실행).

## 환경 변수 (.env)

`.env.example`을 복사해서 채웁니다. 필수/선택 키 목록과 발급처는 파일 내 주석을 참고하세요.

| 키 | 용도 |
|---|---|
| `MONGODB_URI` | MongoDB 접속 문자열 |
| `TAVILY_API_KEY` | 웹 크롤링 (Tavily) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 크롤링 |
| `NIM_KEY` | NVIDIA NIM LLM (분석/RAG 응답 생성) |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 네이버 데이터랩 (트렌드 판정, 1차 신호) |
| `KAKAO_REST_API_KEY` | 카카오 블로그/카페 검색 (트렌드 판정, 2차 신호) |
| `QDRANT_HOST` / `QDRANT_PORT` | Qdrant 접속 (기본값: `qdrant` / `6333`, Docker 네트워크 기준) |

## 디렉토리 구조

| 디렉토리 | 역할 |
|---|---|
| `crawlers/` | 소스별 크롤러(tavily, youtube, namuwiki, natepann, dcinside) |
| `trend/` | 트렌드 판정 (Naver/Kakao/Google 시그널 앙상블) |
| `preprocessing/` | 텍스트 정제 + 구조 인식 청킹 |
| `embedding/` | BGE-M3 인코딩 + Qdrant 적재 파이프라인 |
| `analysis/` | 키워드 분석 프롬프트 + RAG 검색/프롬프트 조립 |
| `eval/` | 검색 방식 비교 평가 하네스 (단위 테스트 아님) |
| `DB/` | MongoDB / Qdrant 클라이언트 |
| `config/config_cilent.py` | 모든 설정값(DB명, 크롤링 한도, 청크 크기, 모델명 등)의 단일 소스 |
| `scheduler/` | 크론 데몬 (Docker 컨테이너의 기본 실행 명령) |


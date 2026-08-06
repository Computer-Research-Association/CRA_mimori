# mimori (memeory)

한국 인터넷 밈/신조어(예: "야르", "쌰갈")를 여러 커뮤니티에서 크롤링 → 정제/청킹 →
관련성 판정(judge) → 임베딩(Qdrant)한 뒤, LLM으로 뜻/유래/뉘앙스를 분석하거나
RAG로 자유 질문에 답하는 파이프라인.

> 이 README는 **로컬(자기 컴퓨터)에서 돌리는 기준**으로 작성했다. 실 서비스는 별도 EC2
> 인스턴스에서 24시간 자동 실행되며, 로컬 환경과 완전히 분리돼 있다(로컬 작업이 실 서비스
> 데이터에 영향을 주지 않는다). EC2 관련 내부 정보(주소 등)는 이 파일에 없다.

## 1. 처음 설정

```bash
git clone <이 저장소>
cd memeory
uv sync                        # pyproject.toml + uv.lock 기준 의존성 설치
cp .env.example .env           # (PowerShell: copy .env.example .env)
# .env를 열어서 TAVILY_API_KEY 등 실제 키를 채운다
docker compose up -d           # mongo + qdrant + flask(스케줄러) 컨테이너 기동
```

`docker-compose.yml`의 Qdrant 포트 매핑(`16333:6333`)은 **Windows가 6333번대를 예약 포트로
막아서 생긴 로컬 전용 우회**다. 앱 자체는 도커 내부 네트워크(`QDRANT_HOST=qdrant`,
`QDRANT_PORT=6333`)로 붙기 때문에 이 호스트 포트 번호와 무관하게 동작한다 — 신경 쓸 필요
없음. Linux(EC2 등)에서는 이 우회 자체가 필요 없다.

## 2. 파이프라인 구조

```
[1] 크롤링 (main.py)
    tavily / youtube / namuwiki / natepann / dcinside / todayhumor
        └─ 각 크롤러가 직접 MongoDB "memes" 컬렉션에 저장
             ▼
[2] 전처리+청킹+judge (preprocess_main.py 또는 preprocess_embed_main.py)
    memes(is_embedded=False) 읽음 → 정제 + 청크 분할 + 문서 단위 관련성 판정
        └─ "cleaned_memes" 컬렉션에 upsert
             ▼
[3] 임베딩 (embed_main.py 또는 preprocess_embed_main.py)
    cleaned_memes의 청크를 BGE-M3로 인코딩(dense+sparse) → Qdrant "mimori_chunks"에 적재
        └─ 성공 시 memes.is_embedded = True
             ▼
[4] 분석(analyze_main.py) / RAG 질의응답(rag_main.py)
```

크롤링 → 전처리 → 임베딩은 완전히 분리된 배치 단계이고, 각자 `memes.is_embedded` 플래그로
"누가 아직 안 처리됐는지"를 스스로 찾아 처리한다.

## 3. 각 단계 실행

| 스크립트 | 하는 일 |
|---|---|
| `python main.py` | 크롤링 (전체 소스 병렬, `crawlers/Keywords.md`의 키워드 전부) |
| `python preprocess_main.py [키워드]` | 정제+청킹+judge (대화형, 키워드 생략 시 전체) |
| `python embed_main.py` | 임베딩+Qdrant 적재 (대화형) |
| `python preprocess_embed_main.py` | 위 둘을 비대화형으로 순서대로(스케줄러가 쓰는 것과 동일) |
| `python analyze_main.py` | 키워드 하나 골라 LLM 분석(뜻/유래/뉘앙스) |
| `python rag_main.py` | 키워드+자유 질문 → RAG 답변 |
| `python reprocess_trigger_main.py --keyword X` | 문서를 재처리 대상으로 되돌림(`is_embedded=False`) |

## 4. 데이터 품질 검증 도구

**`quality_test/` — Mongo/Qdrant 없이 파일 기반으로 정제/청킹/judge 품질을 측정**한다.
자세한 사용법은 [`docs/quality-test-usage.md`](docs/quality-test-usage.md) 참고.

```bash
uv run python quality_test_main.py dump --limit 1000   # Mongo에서 fixture 스냅샷 뜨기(1회)
uv run python quality_test_main.py run --name before    # 스냅샷을 파이프라인에 통과시켜 측정
uv run python quality_test_main.py report before        # 결과 요약
uv run python quality_test_main.py inspect before        # 나쁜 청크 실물 확인
```

**`scripts/` — 실제 크롤러를 키워드로 돌려보고 Mongo/로컬 파일에 남기는 도구.**
호스트에서 직접 실행할 땐 `.env`의 도커 내부 호스트명이 안 통하므로 `MONGODB_URI`를
반드시 덮어써야 한다:

```bash
# bash
MONGODB_URI=mongodb://localhost:27017 uv run python scripts/live_crawl_test.py 쌰갈
MONGODB_URI=mongodb://localhost:27017 uv run python scripts/export_live_results.py 쌰갈 45
```
```powershell
# PowerShell
$env:MONGODB_URI = 'mongodb://localhost:27017'
uv run python scripts/live_crawl_test.py 쌰갈
uv run python scripts/export_live_results.py 쌰갈 45
Remove-Item Env:MONGODB_URI
```

`export_live_results.py`의 두 번째 인자(예: 45)는 "최근 몇 분 내 크롤링된 걸 신규로 볼지"다.
결과는 `data_test/live_test_raw.jsonl`(원본), `data_test/live_test_processed.jsonl`(정제+청킹+judge
결과)에 저장된다.

## 5. 테스트

빠른 순수 로직 회귀 테스트(네트워크/DB 불필요, `tests/`에 13개):

```bash
PYTHONIOENCODING=utf-8 uv run python tests/test_matching.py
# tests/ 안의 다른 test_*.py도 같은 방식
```

각 파일 마지막에 `ALL PASS ✅`가 찍히면 통과.

## 6. 참고 문서

- [`docs/quality-test-usage.md`](docs/quality-test-usage.md) — 품질 측정 도구 상세 사용법
- [`docs/pipeline-before-after-summary-2026-08-04.md`](docs/pipeline-before-after-summary-2026-08-04.md) — 파이프라인 개선 종합 정리(이전/이후/의의/한계)
- [`PROJECT_STUDY.md`](PROJECT_STUDY.md) — 개인 학습 메모(팀 공유용 아님, EC2 관련 내부 정보 포함이라 별도 관리)

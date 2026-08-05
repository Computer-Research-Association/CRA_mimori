# 파이프라인 품질 계측 설계 (1단계)

> 작성 2026-08-03. 브랜치 `feature/data_update`.
> 선행 조사: `docs/pipeline-current-state-2026-08-03.md`
> 이 스펙의 범위는 **1단계(관측 기반 + 안전한 재처리)**. 2·3단계는 별도 스펙으로 진행한다.

---

## 1. 배경

크롤링 → 청킹 → 임베딩 파이프라인에서 나오는 데이터의 품질을 **아무도 측정할 수 없다.**

각 단계가 `print`로 요약을 뱉고 끝난다.

```python
# preprocessing/pipeline.py:70
print(f"[전처리] 처리: {processed}개 문서 / 스킵(빈 본문): {skipped}개 / 생성된 청크: {len(all_chunks)}개")
```

문제가 셋이다.

- **stdout으로 사라진다.** 스케줄러가 매일 04:30에 돌리는데 어제 숫자를 알 방법이 없다.
- **"몇 개"만 있고 "왜"가 없다.** 청크 8,930개 중 몇 개가 30자 미만인지, 몇 개가 스팸인지 모른다.
- **결과물을 볼 수 없다.** 나쁜 청크의 실물을 보려면 매번 Mongo 쿼리를 짜야 한다.

그래서 **"고쳤더니 나아졌나?"에 답할 수 없다.** 이것이 이후 모든 개선의 선행 조건이다.

추가로, 선행 조사에서 **당장 고쳐야 할 결함 두 개**가 확인됐다.

- **고아 청크**: `embedding/pipeline.py`에 delete가 없어, 청크 수가 줄어드는 재처리를 하면 옛 point가 검색 가능한 상태로 잔류한다. 인메모리 Qdrant로 재현 완료.
- **정규화 드리프트**: `analysis/rag_pipeline.py:54`가 개행을 제거하지 않아 `거제 야호~` 키워드 문서가 RAG 검색에서 조용히 탈락 중이다. 실측 확인 완료.

---

## 2. 목표 / 비목표

### 목표

1. 파이프라인 각 단계의 **입력 / 출력 / 사유별 드롭 수**를 기록하고 조회할 수 있게 한다.
2. 청크마다 **품질 신호**(길이·한글비율·스팸·상투어·키워드 매치)를 계산해 남긴다.
3. 개선 전후를 **같은 입력으로** 비교할 수 있게 한다.
4. 재처리가 고아 청크를 만들지 않게 한다.
5. 정규화 드리프트 버그를 고치고 재발을 막는다.

### 비목표 (1단계에서 하지 않음)

- relevance judge 구현 → 2단계
- 크롤러 `_is_relevant` 교체 → 2단계
- 청커 본문/댓글 분리, 최소 길이 변경 → 3단계
- 스팸/상투어 **제거** — 1단계는 **세기만** 한다
- EC2 배포, Mongo `pipeline_runs` 저장 — 로컬 검증 완료 후

**1단계는 파이프라인의 판정 로직을 바꾸지 않는다.** 계측을 붙이고, delete를 추가하고, 정규화 버그를 고칠 뿐이다. 그래야 2·3단계의 효과를 깨끗하게 측정할 수 있다.

> 예외 1건: 정규화 수정은 RAG 검색 결과를 바꾼다(지금 탈락하던 문서가 통과하게 됨). 버그 수정이므로 의도된 변화이며, 리포트에 기록으로 남긴다.

---

## 3. 격리 전략

개발 중에는 **EC2와 프로덕션 데이터를 건드리지 않는다.**

현재 로컬 개발은 SSH 터널로 EC2의 Qdrant에 붙는다(`analysis/rag_pipeline.py:25` 주석, 로컬 `qdrant_storage/collections/`는 비어 있음). 따라서 테스트 실행이 곧 프로덕션 데이터 변경이다.

| 대상 | 방법 | 외부 의존 |
|---|---|---|
| 정제 / 청킹 / 정규화 | 로컬 JSONL fixture | 없음 |
| Qdrant 동작 (재처리) | `QdrantClient(":memory:")` | 없음 |
| 최종 확인 | dev 머지 후 EC2 배포 | 마지막에만 |

`qdrant-client 1.18.0`의 인메모리 모드로 프로젝트가 쓰는 기능이 전부 동작함을 확인했다 — dense+sparse 네임드 벡터, uuid5 결정론적 id, RRF fusion 하이브리드 검색, 필터 기반 delete.

> 인메모리 모드는 payload 인덱스가 무시된다는 경고를 낸다(필터링 자체는 동작). 인덱스는 실서버 **성능**을 위한 것이므로 로직 검증에는 영향이 없다.

### 컬렉션 이름 격리

필요할 때 테스트 컬렉션으로 돌릴 수 있게 환경변수 override를 넣는다. **기본값이 현재와 같으므로 `.env`를 안 건드리면 동작이 동일하다.**

```python
MONGO_COLLECTION   = os.getenv("MONGO_COLLECTION",   "memes")
CLEANED_COLLECTION = os.getenv("CLEANED_COLLECTION", "cleaned_memes")
QDRANT_COLLECTION  = os.getenv("QDRANT_COLLECTION",  "mimori_chunks")
```

### `_test` 접미사

새로 만드는 폴더에는 `_test`를 붙여 **추가된 것이고 검증 중임을 한눈에** 보이게 한다.

```
quality_test/          새 코드 패키지
quality_test_main.py   CLI 진입점
data_test/             산출물 (fixtures, runs)
```

정식 채택 시 `quality_test` → `quality` 이름 변경이 필요하며, import 약 8줄을 함께 수정한다. 한 커밋으로 끝나는 비용이다.

---

## 4. 모듈 구조

```
quality_test/
  __init__.py
  matching.py     키워드 매칭의 유일한 정의 — normalize / find_keyword
  signals.py      청크 품질 신호 계산
  funnel.py       단계별 카운터 (입력/출력/사유별 드롭)
  fixture.py      Mongo → JSONL 덤프, JSONL 로드    ← DB 접촉 유일 지점
  runner.py       fixture로 파이프라인 실행 → runs 산출
  report.py       run 집계·비교 출력
  inspect.py      run 개별 청크 조회

quality_test_main.py    dump / run / report / inspect 서브커맨드
```

| 모듈 | DB 접촉 | 순수 함수 |
|---|---|---|
| `matching.py` | ❌ | ✅ |
| `signals.py` | ❌ | ✅ |
| `funnel.py` | ❌ | ✅ |
| `runner.py` | ❌ | — |
| `report.py` / `inspect.py` | ❌ | — |
| `fixture.py` | **✅ 여기만** | — |

DB를 만지는 곳이 한 파일뿐이라, 나머지는 목(mock) 없이 파일과 문자열만으로 테스트된다.

---

## 5. 핵심 설계 판단

### 5.1 측정 대상은 프로덕션 코드와 동일해야 한다

`runner.py`가 "정제 → 청킹"을 자체 구현하면 측정한 것과 실제로 도는 것이 다른 코드가 된다. 시간이 지나면 반드시 어긋나고, 리포트 숫자가 거짓말을 하기 시작한다.

`preprocessing/pipeline.py`에서 DB를 안 만지는 알맹이를 함수로 뽑아 양쪽이 공유한다.

```python
# preprocessing/pipeline.py
def process_one(doc: dict) -> tuple[str, list[dict]]:
    """정제 + 청킹. DB 접촉 없음. Mongo 경로와 fixture 경로가 함께 쓴다."""
    clean = clean_text(doc.get("content", ""))
    return clean, chunk_document({**doc, "clean_content": clean})
```

```
preprocess_documents()  ─┐
   (Mongo 읽기/쓰기)     ├─▶  process_one()   ← 한쪽만 고쳐질 수 없다
quality_test/runner.py  ─┘
   (JSONL 읽기/쓰기)
```

### 5.2 키워드 매칭 정의를 하나로

현재 `normalize`가 5곳(크롤러 3 + RAG 1 + 미머지 judge 1)에 있고 동작이 3종류다. 핵심 차이는 **개행 처리**다.

| 위치 | 공백 처리 |
|---|---|
| 크롤러 | `\s` — 개행·탭 포함 전부 제거 |
| RAG (`rag_pipeline.py:54`) | `" ~"` — **스페이스만. 개행 안 지움** |

크롤러는 본문을 `get_text(separator="\n")`로 뽑으므로 인라인 요소 경계마다 개행이 들어간다. `거제 야호~`가 두 요소에 걸치면 텍스트는 `거제\n야호`가 되고, **크롤러는 통과시키지만 RAG는 탈락시킨다.** 에러도 로그도 남지 않는다.

`quality_test/matching.py`에 정의를 한 벌만 둔다.

```python
def normalize(s: str) -> str:
    """NFC → casefold → 모든 공백류(\s, 개행 포함) 제거 → 장식 물결(~〜﹏∼) 제거"""

def find_keyword(keyword: str, title: str, body: str) -> Match:
    """(matched, position: title|early|late|none, count) 반환"""
```

소비자는 셋이다. 1단계에서는 RAG만 교체하고, 크롤러/judge는 2단계에서 교체한다.

```
analysis/rag_pipeline._is_valid_chunk  → matching.normalize      (1단계)
crawlers/*._is_relevant                → matching.find_keyword   (2단계)
preprocessing judge                    → matching.find_keyword   (2단계)
```

`crawlers/`가 `quality_test/`를 import하는 것은 층 역전이 아니다. `matching.py`는 상위 로직이 아니라 문자열 처리 원시함수이며, `config`나 `DB`처럼 어디서든 쓰는 위치다.

### 5.3 신호 계산과 판정을 분리한다

`signals.py`는 **값만 계산하고 판단하지 않는다.** 청크 하나만 보면 전부 계산되는 순수 함수이며,
매치 관련 값은 `matching.find_keyword()`를 호출해 채운다(`signals` → `matching` 단방향 의존).

```python
compute_signals(text, title, keyword) -> {
    "char_count": 71, "hangul_ratio": 0.62, "spam_hits": 3,
    "boilerplate_hits": 0, "match_position": "late", "match_count": 1,
}
```

"이게 나쁜 청크인가"는 별개 함수가 임계값을 가지고 판단한다. **임계값은 계속 바뀌기 때문**이다. 30자가 맞는지 20자가 맞는지는 데이터를 보고 정한다. 나눠두면 저장된 신호에 새 임계값을 적용해보는 것이 공짜다.

임계값은 전부 `config/config_cilent.py`에 모은다.

---

## 6. 품질 판단 계층

완벽한 판단은 불가능하다. 3층으로 나눠 싼 것부터 쓴다.

### 레벨 1 — 라벨 없이 자동으로 (매 실행)

정답을 몰라도 **이상**은 알 수 있다. 회귀 감지가 목적이다.

```
드롭률이 갑자기 변했다        → 사이트 HTML 변경 또는 크롤러 파손
특정 소스가 0건이다           → 차단
청크 길이 중앙값 급락          → 본문 추출 실패
스팸 패턴 비율 상승            → 정제 규칙이 뚫림
```

"좋다"는 못 말해도 **"어제보다 나빠졌다"는 확실히 말한다.**

### 레벨 2 — 나쁨을 규칙으로 특정 (자동)

라벨 없이도 "확실히 나쁘다"는 규칙으로 특정할 수 있다.

| 규칙 | 근거 | 1단계 구현 |
|---|---|---|
| 청크 `char_count` < 30 | 정보 없음 | ✅ |
| 키워드가 제목·본문 어디에도 없음 | 태깅 오류 | ✅ |
| `spam_hits` >= 3 | 광고 | ✅ |
| `hangul_ratio` < 0.3 인데 국내 소스 | 본문 추출 실패 | ✅ |
| 같은 내용 문서가 이미 존재 | 중복 | ❌ **2단계** |

**"나쁜 걸 잡는" 게 아니라 "확실히 나쁜 것만 잡는다."** 애매한 것은 건드리지 않는다. 그래야 정상 문서를 잘못 버리는 사고가 없다.

근접 중복 판정만 1단계에서 뺀다. 나머지는 청크 하나만 보면 계산되는 순수 함수인데, 중복은 **문서 간 비교**가 필요해 전역 상태를 다뤄야 한다. 성격이 다르므로 2단계로 미룬다.

### 레벨 3 — 사람 라벨 (표본, 가끔)

레벨 2 규칙이 진짜 나쁜 것을 잡는지는 사람이 봐야 안다. 여기서만 정밀도/재현율이 나온다. 50~100건을 무작위 추출해 사람이 O/X 하고 규칙 판정과 대조한다. 비용이 있으므로 규칙을 크게 바꿀 때만 재측정한다.

> `origin/feature/rag_test`에 사람 라벨 50건이 있으나, 크롤러 수정 이전 데이터라 분포가 다르다(오염 17건 중 10건이 지금은 크롤 시점에 막히는 natepann). 참고 자료로만 보고 새로 뽑는다.

### 최종 성적표 — RAG 필터 발동률

`analysis/rag_pipeline.py`의 방어 필터가 얼마나 자주 발동하는지가 곧 상류 품질이다. 상류가 완벽하면 필터는 아무것도 걸러선 안 된다. 합성 지표가 아니라 실제 질의에 대한 성적표라 가장 믿을 만하다. (기록 자체는 2단계 이후에 붙인다.)

---

## 7. 산출물 형식과 저장 위치

```
C:\workspace\memeory\
├── quality_test/              코드. 커밋함
├── quality_test_main.py       코드. 커밋함
└── data_test/                 산출물. .gitignore 에 추가 (커밋 안 함)
    ├── fixtures/
    │   └── raw_sample.jsonl
    └── runs/
        ├── before/  cleaned.jsonl  chunks.jsonl  funnel.json
        └── after/   ...
```

커밋하지 않는 이유: 용량(run당 5~10MB, 반복 시 누적), 내용(크롤링한 게시글 원문), 재생성 가능(코드만 있으면 복원).

경로는 `config_cilent.py`의 기존 방식대로 **프로젝트 루트 기준으로 고정**한다. cwd 기준이면 다른 폴더에서 실행 시 엉뚱한 곳에 쌓인다.

```python
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_TEST_DIR = os.path.join(_ROOT, "data_test")
FIXTURE_DIR   = os.path.join(DATA_TEST_DIR, "fixtures")
RUNS_DIR      = os.path.join(DATA_TEST_DIR, "runs")
```

### `chunks.jsonl` — 한 줄이 청크 하나

```json
{
  "parent_id": "02ffb4a4...", "chunk_index": 3,
  "text": "야르\n\n[댓글]\n⭐ 라. 인: King...",
  "keyword": "야르", "source": "natepann", "url": "...", "title": "퇴근버스",
  "signals": {
    "char_count": 71, "hangul_ratio": 0.62, "spam_hits": 3,
    "boilerplate_hits": 0, "match_position": "late", "match_count": 1
  }
}
```

### `funnel.json` — 단계별 카운터

```json
{
  "run": "before", "fixture": "raw_sample.jsonl",
  "stages": {
    "clean": {"in": 1240, "out": 1232, "dropped": {"empty_content": 8}},
    "chunk": {"in": 1232, "out": 8930, "dropped": {}}
  }
}
```

**청커 내부 드롭은 funnel에 안 잡힌다.** `chunk_document()`가 10자 미만 청크를 버리면서
개수를 반환하지 않기 때문이다(`preprocessing/chunker.py:81`). 이걸 잡으려면 함수 시그니처를
바꿔야 하는데 1단계 비목표(판정 로직 불변)에 어긋난다.

대신 **살아남은 청크의 신호 분포**로 본다 — "30자 미만이 2,180개(24.4%)"는 `chunks.jsonl`의
`signals.char_count`를 집계하면 나온다. 3단계(최소 길이 10→30)에서 필요한 숫자가 정확히
이것이므로 실질적 손실이 없다. 청커를 손대는 3단계에서 드롭 카운터를 함께 넣으면 된다.

JSONL을 쓰는 이유: 한 줄씩 읽을 수 있어 파일이 커져도 메모리에 다 올리지 않아도 되고, `grep`으로도 볼 수 있다. `analysis/eval_runs.jsonl`이 이미 같은 형식이다.

### 운영(EC2)에서는 다르게

| | 개발 (로컬) | 운영 (EC2) |
|---|---|---|
| 목적 | 개선 전후 비교 | 매일 회귀 감지 |
| 남기는 것 | 청크 실물까지 전부 | funnel 요약만 |
| 저장 위치 | `data_test/runs/` 파일 | Mongo `pipeline_runs` |
| 용량 | run당 5~10MB | 실행당 수 KB |

`funnel.py`가 만드는 dict는 하나이고 **쓰는 곳(sink)만 다르다.** 로직이 갈라지지 않으므로 "로컬에선 되는데 서버에선 다른 숫자" 사고가 없다. (운영 저장은 1단계 범위 밖.)

---

## 8. 기존 파일 변경점

| 파일 | 변경 | 예상 크기 |
|---|---|---|
| `config/config_cilent.py` | 컬렉션 env override, `DATA_TEST_DIR` 등 경로, 품질 임계값 상수 | ~15줄 |
| `DB/drant_clitent.py` | `parent_id` · `source` payload 인덱스 추가 | ~8줄 |
| `embedding/pipeline.py` | upsert 전 `parent_id` filter delete (항상 실행) | ~10줄 |
| `preprocessing/pipeline.py` | `process_one()` 추출 + funnel 카운터 연결 | ~15줄 |
| `analysis/rag_pipeline.py` | `_normalize_for_match` → `matching.normalize` | ~5줄 |
| `.gitignore` | `data_test/` 추가 | 1줄 |

delete는 **"재처리할 때만"이 아니라 "항상"** 실행한다. 조건부로 만들면 언젠가 조건이 틀린다. 항상 지우고 넣으면 몇 번을 돌려도 상태가 같아진다(멱등). 비용은 문서당 delete 호출 1회다.

---

## 9. 완료 기준

말이 아니라 **실행해서 확인되는 것**으로만 정의한다.

### 9.1 현존 버그가 테스트로 잡힌다

```python
# tests/test_matching.py
def test_키워드가_개행으로_쪼개져도_매칭된다():
    assert find_keyword("거제 야호~", title="", body="외치는 거제\n야호 에서").matched
```

**이 테스트는 수정 전 코드에서 실패해야 한다.** 먼저 실패를 확인하고(버그가 실재한다는 증거), 고친 뒤 통과시킨다. 통과만 확인하면 테스트가 애초에 아무것도 검사하지 않는 경우를 구분할 수 없다.

### 9.2 고아 청크가 0이 된다

```python
# tests/test_reembed.py — QdrantClient(":memory:"), 외부 의존 없음
def test_재적재_시_고아_청크가_없다():
    # 청크 8개 적재 → 5개로 재적재
    assert client.count(COLL).count == 5           # delete 없으면 8
    assert not any("구버전" in p.payload["text"] for p in scroll())
```

재현과 해결 모두 인메모리 Qdrant로 검증 완료. 그대로 테스트로 옮긴다.

### 9.3 리포트가 실제 숫자를 낸다

`report before` 실행 시 **현재 아무도 모르는 숫자**가 출력된다. 아래는 출력 **형식** 예시이며,
숫자는 임의값이다 — 실제 값은 돌려봐야 안다.

```
청크                       8,930
  ├ 30자 미만              2,180  (24.4%)
  ├ 스팸 패턴 포함            733  ( 8.2%)
  └ 키워드 미포함              91  ( 1.0%)
청크 길이 중앙값               41
```

숫자 자체는 개선 전이라 나쁠 것이며, **그것이 기준선이다.**

### 9.4 기존 동작이 안 바뀐다

```bash
python -c "from config.config_cilent import MONGO_COLLECTION, QDRANT_COLLECTION; \
           print(MONGO_COLLECTION, QDRANT_COLLECTION)"
# → memes mimori_chunks
```

`.env`를 안 건드리면 컬렉션 이름이 현재와 동일해야 한다.

### 9.5 전체 테스트 통과

이 프로젝트에는 pytest가 설치돼 있지 않다. 기존 테스트(`tests/test_crawl_parallel.py`)는
순수 파이썬 스크립트로, `assert` + `print("[OK] ...")` + 하단 `if __name__ == "__main__":`
러너 형태다. **새 테스트도 같은 관례를 따른다** — 의존성을 늘리지 않고 `uv run python`만으로 돈다.

```bash
uv run python tests/test_matching.py
uv run python tests/test_signals.py
uv run python tests/test_funnel.py
uv run python tests/test_reembed.py
```

각 파일 마지막 줄에 `ALL PASS ✅`가 찍히면 통과다.

---

## 10. 구현 순서

의존 관계상 이 순서가 자연스럽다. **중간에 멈춰도 버려지는 작업이 없다.**

```
1. matching.py + test_matching.py          버그 재현 → 수정 → 통과
2. rag_pipeline.py 를 matching 으로 교체     버그 해소 확인
3. signals.py + test_signals.py            신호 계산
4. fixture.py + dump 서브커맨드              실제 데이터 확보
5. funnel.py + runner.py + run 서브커맨드    측정 가능해짐
6. report.py / inspect.py                  볼 수 있게 됨  ← 기준선 확보
7. drant_clitent.py payload 인덱스
8. embedding delete + test_reembed.py      재처리 안전해짐
```

6번까지면 이미 쓸모가 있다. 7·8은 2·3단계를 위한 준비이므로 그 앞에만 있으면 된다.

---

## 11. 이후 단계 (참고)

```
[2단계] 품질 게이트
  - relevance judge 직접 구현 (짧은 키워드 처리 + 매치 횟수 포함)
  - 크롤러 _is_relevant → matching.find_keyword 로 수렴

[3단계] 청킹 개선
  - 본문/댓글 항상 분리 + content_type 필드
  - 청크 최소 길이 10 → 30
```

짧은 키워드 문제: `crawlers/Keywords.md`에 `67`이 있다. 부분문자열 매칭이므로 게시글 번호·가격·조회수 어디에 `67`이 들어가도 통과한다. 2단계에서 별도 처리 규칙이 필요하다.

3단계 순서 주의: 청커 최소 길이 변경은 청크 수를 줄이므로, **1단계의 delete-before-upsert가 반드시 먼저** 들어가 있어야 한다. 그렇지 않으면 걸러내려던 저품질 청크가 고아로 잔류해 검색 가능한 상태로 고착된다.

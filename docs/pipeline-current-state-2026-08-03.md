# 크롤링 · 청킹 · 임베딩 — 현재 수준 점검과 개선 로드맵

> 작성 2026-08-03. 기준 브랜치 `dev` (`33232db`).
> 선행 문서: `docs/crawler-contamination-findings.md`(7월 조사), `docs/pipeline-improvement-notes.md`(7월 개선안),
> `docs/automation-pipeline.md`(자동화 구조).
>
> **이 문서를 먼저 읽어야 하는 이유**: 위 두 조사 문서는 스스로 "그 당시 발견 기록"이라고 밝히고 있고,
> 실제로 그 이후 코드가 상당히 움직였다. 문서에 적힌 개선안 중 **이미 반영된 것, 다른 브랜치에
> 구현돼 있지만 머지되지 않은 것, 아직 손대지 않은 것**이 섞여 있어 그대로 작업 목록으로 쓰면
> 이미 끝난 일을 다시 하게 된다. 아래는 전부 `dev`의 현재 코드를 직접 읽고 대조한 결과다.

---

## 0. 요약 (먼저 읽을 3가지)

1. **선행 문서의 크롤러 개선안은 대부분 이미 반영됐다.** 관련성 게이트, 날짜 파싱 폴백 구멍,
   최소 길이 필터, tavily 쿼리/점수 하한선, youtube 키워드 필터 — 전부 `dev`에 들어와 있다
   (2절). 아직 남은 건 **청킹·임베딩 단계**와 데이터 품질 정제 쪽이다.

2. **가장 큰 발견 — "전처리 judge"는 이미 만들어져 있는데 머지가 안 됐다.**
   `dev`의 주석 두 곳(`config/config_cilent.py:22`, `crawlers/youtube_crawler.py:101`)이
   "정밀 판정은 전처리 judge가 담당한다"며 느슨한 상류 필터를 정당화하는데,
   **`dev`에는 그 judge가 없다.** 실물은 `origin/feature/rag_test`의 `preprocessing/relevance.py`에
   있고 dev로 머지된 적이 없다. 즉 현재 tavily/youtube의 잔여 오염은 **설계상으로만** 걸러지고
   실제로는 아무도 안 거른다 (3절). 라벨셋 50건으로 측정해보니 이 judge는 정밀도 0.805 / 재현율 1.000이고,
   간단한 개선으로 정밀도 0.889까지 오른다 (3.3절).

3. **같은 검증 로직이 여러 벌 존재하고, 서로 어긋나 있다 — 그리고 지금 실제로 터지고 있다.**
   크롤러는 정규화 때 개행을 지우는데 RAG는 안 지운다. 그래서 **`거제 야호~` 키워드 문서가
   크롤러는 통과하고 RAG 검색에서는 조용히 탈락한다**(4.1에 실측). 에러도 로그도 안 남는다.
   길이 임계값도 30(크롤러, 문서 단위) / 10(청커, 청크 단위) / 30(RAG, 청크 단위)으로 따로 논다 (4절).

> 🚨 **작업 착수 전 반드시 4.4절을 읽을 것.** 임베딩 파이프라인에 delete가 없어서,
> 청킹 로직을 고친 뒤 재처리하면 옛 청크가 Qdrant에 고아로 남아 계속 검색된다.
> "청크 품질 개선"이 오히려 저품질 청크를 고착시키는 결과가 되는 지뢰가 있다.

---

## 1. 현재 파이프라인 전체 그림

```
crawlers/*.py  ──▶ Mongo memes (is_embedded=False)
   tavily / youtube / namuwiki / natepann / dcinside
   [관련성 게이트: 4개 소스 O, namuwiki X]
   [날짜 컷오프: natepann/dcinside O, youtube/tavily/namuwiki X]
        │
        ▼  preprocessing/pipeline.py  (스케줄러: 매일 04:30)
   cleaner.clean_text()  →  chunker.chunk_document()
   [정제: HTML/URL/이메일/전화/반복문자/공백]
   [청킹: 나무위키=섹션 기반, 그 외=재귀 분할, 청크 <10자 버림]
        │
        ▼  Mongo cleaned_memes (원본+clean_content+chunks)
        │
        ▼  embedding/pipeline.py
   encoder.encode_batch()  BGE-M3 dense(1024) + sparse
        │
        ▼  Qdrant mimori_chunks  →  memes.is_embedded=True
        │
        ▼  analysis/rag_pipeline.py  (질의 시점)
   [하이브리드 검색 + 오염 방어 필터 5종]
```

품질 검증이 **크롤 시점**과 **질의 시점** 양 끝에만 있고, 그 사이(청킹·임베딩)에는 거의 없다는 게
현재 구조의 핵심 특징이다. 선행 문서가 제안한 "적재 시점 검증"이 바로 이 빈 구간을 메우자는 얘기다.

---

## 2. 선행 문서 대비 — 이미 반영된 항목

`docs/crawler-contamination-findings.md`와 `pipeline-improvement-notes.md`의 제안 중
**아래는 이미 `dev`에 들어와 있다. 다시 작업하지 말 것.**

| 선행 문서 항목 | 현재 상태 | 근거 |
|---|---|---|
| 검색 결과 무검증 신뢰 (오염 문서 1번) | **해결** — 제목+본문에 키워드 없으면 저장 제외 | `crawlers/natepann_crawler.py:46,225`, `crawlers/dcinside_crawler.py:51,272` |
| 날짜 파싱 실패 → 최근 글 간주 폴백 (2번) | **해결** — `None`이면 `continue`로 건너뛰고 `parse_failed` 카운터로 로깅 | `natepann_crawler.py:116-120`, `dcinside_crawler.py:126-130` |
| 감탄사성 한 줄 글 수집 (사례 2) | **해결** — `MIN_CONTENT_LEN = 30` 문서 단위 게이트 | `natepann_crawler.py:33,219`, `dcinside_crawler.py:38,266` |
| tavily 쿼리가 SEO 콘텐츠 팜을 낚음 (개선안 1.1) | **부분 해결** — 키워드를 따옴표로 감싸 구문 일치 우선 + relevance score 하한선 0.3 | `tavily_crawler.py:55,73`, `config_cilent.py:23` |
| youtube 관련성 판별 없음 | **해결** — 제목+설명 키워드 필터, 댓글 조회 **전에** 걸러 쿼터 절약 | `youtube_crawler.py:97,183` |

> 참고로 선행 문서의 "SOURCES에서 tavily/youtube만 제외하는 근거가 약하다"는 지적도
> 이 변경들로 전제가 바뀌었다. 이제 5개 소스 중 4개가 같은 종류의 게이트를 갖고 있어,
> 소스 단위 배제보다 **문서 단위 품질 라벨**로 가는 게 자연스럽다 (→ 3절).

---

## 3. 최대 이슈 — 만들어졌지만 머지되지 않은 "전처리 judge"

### 3.1 유령 참조

`dev`의 두 주석이 존재하지 않는 단계를 전제로 하고 있다.

`config/config_cilent.py:21-23`
```python
# Tavily relevance score 하한선. 기존 수집분의 is_relevant 라벨 기준
# 0.3에서 정상 문서 89% 유지 / 오염 문서 58% 차단 — 잔여 오염은 전처리 judge가 거름.
TAVILY_MIN_SCORE = 0.3
```

`crawlers/youtube_crawler.py:97-103`
```python
def is_keyword_relevant(keyword: str, title: str, description: str) -> bool:
    """...
    정밀 판정은 전처리 단계의 judge가 담당하므로 여기서는 완벽할 필요 없음.
    """
```

**그런데 `preprocessing/`에는 judge가 없다.** `preprocessing/pipeline.py`는 정제→청킹→저장만 한다.
`main.py:82`의 `judge_and_report()`는 이름만 비슷할 뿐 **트렌드(z-score) 판정**이라 무관하다.

즉 tavily는 자기가 놓치는 오염 42%를, youtube는 느슨한 제목/설명 매칭이 놓치는 걸
"뒤에서 걸러줄 것"이라 믿고 통과시키는데 **뒤에 아무도 없다.** RAG 단계가 그 부담을
떠안게 된 직접적인 원인이다.

### 3.2 실물은 `feature/rag_test`에 있다

```
origin/feature/rag_test  (3e35e7c, 미머지)
  preprocessing/relevance.py          ← judge 본체 (93줄)
  preprocessing/pipeline.py           ← 청킹 직후 judge 호출, chunk에 is_relevant 부착
  embedding/pipeline.py               ← payload에 is_relevant / relevance_position 추가
  eval/relevance_eval.py, eval/judge.py, eval/metrics.py, eval/run_compare.py
  eval/results/relevance_labelset.csv ← 사람이 라벨링한 50건
```

관련 커밋: `fe7cab8`(판정기+하네스), `d7e20ac`(라벨셋 어노테이션), `5b13618`(청킹 시 자동 판정 통합),
`88a94ff`(RAG 필터 활성화), `e75a49e`(Qdrant 백필), `37628c7`(backfill 서브커맨드).

**이것이 곧 `pipeline-improvement-notes.md`의 개선안 3.1(품질 필드를 payload에 미리 계산)과
3.3(keyword 태그 검증을 적재 시점으로 이동)의 완성된 구현이다.** 새로 만들 필요가 없다.

judge의 설계 특징(선행 문서보다 한 발 앞선 부분):
- **doc 단위 판정**: 청크 단위로 보면 "그 밈을 다루지만 그 청크엔 키워드 글자가 없는" 정상 청크를
  구조적으로 오탐하므로, 제목+전체 청크를 이어붙여 한 번이라도 매치되면 관련으로 본다.
- **부분 매칭 안 함**: "거제야호"를 "야호"로 부분매칭해 10년 전 여행 후기를 끌어온 오염을 재현하지 않기 위해
  정규화 후 **전체 키워드**가 통으로 등장해야 한다.
- **매치 위치 기록**: `title` / `early`(본문 200자 내) / `late` / `none`을 payload에 남긴다.

### 3.3 라벨셋으로 실제 측정해봤다 (50건)

`eval/results/relevance_labelset.csv`의 `judge_matched` vs `human_label`을 직접 대조한 결과:

| 판정 기준 | TP | FP | FN | TN | 정밀도 | 재현율 |
|---|---|---|---|---|---|---|
| **현재 구현** (matched=1 전부 통과) | 33 | 8 | 0 | 9 | **0.805** | **1.000** |
| `late` 위치 제외 (title+early만 통과) | 32 | 4 | 1 | 13 | **0.889** | **0.970** |
| `title`만 통과 | 24 | 0 | 9 | 17 | 1.000 | 0.727 |

위치별 분포를 보면 신호가 뚜렷하다:

| 위치 | 관련(1) | 오염(0) |
|---|---|---|
| `title` | 24 | 0 |
| `early` | 8 | 4 |
| `late` | 1 | 4 |

`relevance.py`의 독스트링은 *"위치 가중치가 필요한지 라벨셋으로 판단하기 위해, 지금은 가중치를
넣지 않고 위치만 기록한다"*고 적어뒀는데, **라벨셋이 그 방향을 시사한다**:
`late` 위치는 5건 중 4건이 오염이다. 정확히 문서가 예상했던 "tavily의 긴 SEO 문서가 다른 밈을
나열하다 본문 후반에 키워드를 한 번 흘리는" 패턴이다. `late`를 제외하면 오탐 8→4로 절반이 줄고
정상 문서는 1건만 잃는다.

#### ⚠️ 이 숫자를 그대로 "현재 상태"로 읽으면 안 된다

**(1) 라벨셋은 크롤러 수정 이전 데이터로 만들어졌다.** 오염(`human_label=0`) 17건의 소스 분포는
natepann 10 / youtube 5 / tavily 2인데, natepann은 **지금은 크롤 시점에 `_is_relevant` +
`MIN_CONTENT_LEN=30`으로 막히는 소스다**(2절). 저 10건은 오늘이라면 애초에 수집되지 않는다.
따라서 0.805는 "현재 데이터에서 judge가 이만큼 거른다"가 아니라 **"7월 데이터 기준이고, 그중
상당수는 이제 상류에서 막힌다"**로 읽어야 한다.

그럼에도 **judge만이 커버하는 잔여 구간은 tavily(2) + youtube(5)로 여전히 실재한다** —
정확히 3.1절의 유령 참조가 방치하고 있는 그 구간이다. 그래서 우선순위 1번은 그대로 유효하다.

**(2) `late` 규칙은 in-sample 선택이다.** CSV의 `judge_matched`는 개발 중이던 judge가 스스로
찍은 값이고, `late` 규칙은 같은 50건 중 5건을 보고 고른 것이다. 표본 밖에서 같은 이득이 난다는
보장이 없다. 그래서 이 항목은 우선순위 5번에 두고 **라벨셋 100건 재측정을 통과 조건으로** 걸었다.

### 3.4 주의 — 그냥 머지하면 안 된다

`feature/rag_test`는 `dev`보다 **오래된** 브랜치다. 단순 머지하면 그 이후 dev에 들어온 작업이
되돌아간다. `git diff dev origin/feature/rag_test` 기준으로 되돌아가는 것들:

- `CRAWL_WORKERS` / 병렬 크롤 구조 (`main.py`, `crawlers/base.py`의 도메인별 RateLimiter)
- `TAVILY_MIN_SCORE`, tavily 따옴표 쿼리, youtube 키워드 필터
- natepann/dcinside의 `_is_relevant` 게이트와 날짜 파싱 수정
- `preprocess_embed_main.py` (스케줄러 진입점), `docs/` 3개 문서, `tests/test_crawl_parallel.py`

**필요한 건 머지가 아니라 cherry-pick**이다. 가져올 파일은 사실상 4개뿐:
`preprocessing/relevance.py`(신규), `preprocessing/pipeline.py`(+14줄), `embedding/pipeline.py`(+2줄),
`eval/` 디렉터리(신규, 기존 코드 미접촉).

---

## 4. 구조적 결함 — 같은 검증이 여러 벌, 서로 다르게 동작

### 4.1 정규화 규칙이 드리프트했다

| 위치 | 제거 대상 | 공백 처리 | 추가 처리 |
|---|---|---|---|
| `natepann_crawler.py:43` / `dcinside_crawler.py:48` / `youtube_crawler.py:94` | `[\s~!?.,'"…]` | `\s` — **개행·탭 포함 전부** | `.lower()` |
| `analysis/rag_pipeline.py:54-60` | `" ~"` | **스페이스만. 개행·탭은 안 지움** | 없음 |
| `preprocessing/relevance.py`(미머지) | `~〜﹏∼` | `\s+` — 개행·탭 포함 전부 | NFC + `casefold()` |

**정의는 5곳(크롤러 3 + RAG 1 + judge 1), 동작은 3종류다.**

#### 이건 이론적 위험이 아니라 지금 터지고 있는 버그다

핵심 차이는 문장부호가 아니라 **개행**이다. 현재 `Keywords.md`에 문장부호가 든 키워드는 없지만,
**공백이 든 키워드는 있다 — `거제 야호~`**.

크롤러는 본문을 `get_text(separator="\n")`로 뽑는다(`natepann_crawler.py:168`,
`dcinside_crawler.py:169`). 즉 인라인 요소 경계마다 **개행이 들어간다.** 원문에서
"거제 야호~"가 두 요소에 걸쳐 있으면 텍스트는 `거제\n야호`가 된다.

실측:

```
텍스트: "미나미가 외치는 거제\n야호 에서 야호는"   (개행 포함)
  크롤러 게이트: 통과   (\s가 개행을 지워 "거제야호" 매칭 성공)
  RAG   게이트: 탈락   (스페이스만 지워 "거제\n야호" ≠ "거제야호")

텍스트: "미나미가 외치는 거제 야호 에서 야호는"    (스페이스만)
  크롤러 게이트: 통과 / RAG 게이트: 통과
```

즉 **크롤러가 정상 수집한 "거제 야호~" 문서가 RAG 검색에서 조용히 탈락한다.** 에러도 로그도
남지 않고, 그냥 검색 결과가 비거나 백필로 채워진다. 선행 문서가 이걸 "중복"으로만 지적했는데
실제로는 **중복이면서 동시에 서로 어긋나 있고, 그 어긋남이 실제 키워드에서 발동 중**이다.

새 사본을 만들지 말고 `preprocessing/relevance.py:normalize()` 하나로 수렴시켜야 한다
(`\s+` + NFC + casefold까지 하는 그 구현이 셋 중 유일하게 이 케이스를 처리한다).
judge를 cherry-pick하면(우선순위 1번) 자연히 해결되는 문제이기도 하다.

### 4.2 길이 임계값 3개가 협응하지 않는다

| 위치 | 값 | 단위 |
|---|---|---|
| `natepann_crawler.py:33`, `dcinside_crawler.py:38` | 30자 | **문서**(본문+댓글 합산) |
| `preprocessing/chunker.py:81` | 10자 | **청크** |
| `analysis/rag_pipeline.py:50` | 30자 | **청크** |

크롤러가 문서 단위로 30자를 보장해도, 청커가 그 문서를 다시 쪼개면서 10자짜리 청크를 통과시킨다.
그래서 30자 미만 청크를 **RAG만이** 걸러낸다 — 그 청크들은 Qdrant 저장공간을 차지하고
임베딩 비용을 쓴 뒤 질의 때마다 반복해서 탈락한다. 청커 임계값을 RAG와 맞추면
(선행 문서 개선안 2.1) 이 낭비가 통째로 사라진다.

### 4.3 나무위키는 게이트가 없고, 그 대신 title 복사에 의존한다

`namuwiki_crawler.py`에는 관련성 게이트도 날짜 필터도 없다. `/w/{키워드}`로 직접 접근하므로
**설계상 옳다** — 문서 자체가 곧 그 키워드다.

다만 부수 효과가 하나 있다. `chunker.py:90`이 모든 청크에 부모 문서 제목을 복사해 넣기 때문에,
"유래" 섹션 본문에 키워드 글자가 안 나와도 title 매칭으로 RAG 필터를 통과한다.
선행 문서가 경고한 대로 **이 title 복사는 나무위키 롱폼 문서의 RAG 생존에 load-bearing**이다.
`title`을 섹션 제목 용도로 바꾸는 리팩터링을 하면 조용히 깨진다. (`section_title` 필드가 이미
따로 있으므로 굳이 그럴 이유는 없다.)

### 4.4 재처리 경로가 없다 — 청킹 로직을 고쳐도 기존 데이터는 안 바뀐다

`preprocessing/pipeline.py:31`과 `embedding/pipeline.py:88`은 둘 다 `{"is_embedded": False}`만
처리한다. 즉 **청커나 cleaner를 개선해도 이미 적재된 문서는 영원히 옛 로직 결과로 남는다.**

아래 5절의 청킹 개선을 실제로 효과 보려면 재처리가 필수인데, 지금은 그 경로가 없다.
`feature/rag_test`의 `eval/relevance_eval.py`에 `backfill` 서브커맨드가 있으니
(커밋 `37628c7`, `e75a49e`) 그 패턴을 재사용하는 게 가장 빠르다.

#### 🚨 함정: `is_embedded=False`로 되돌리기만 하면 고아 청크가 남는다

이건 재처리를 시도하는 사람이 **거의 확실히 밟게 되는 지뢰**라 따로 적어둔다.

`embedding/pipeline.py`에는 **delete가 한 줄도 없다.** point id는
`uuid5(f"{parent_id}::{chunk_index}")`로 결정론적이므로(`embedding/pipeline.py:38`)
같은 인덱스는 덮어써지지만, **인덱스가 사라지면 옛 point가 그대로 남는다.**

청커 최소 길이를 10→30으로 올리면(우선순위 4번) 대부분의 문서에서 청크 수가 줄어든다.
청크 0..7이던 문서가 0..4가 되면 → 0..4는 덮어써지고 **5..7은 옛 텍스트 그대로 Qdrant에 잔류**한다.
그리고 그 고아 point들은 `keyword` payload를 정상적으로 갖고 있어서 `_build_filter`에 걸리고,
**RAG 검색 결과로 계속 나온다.**

즉 "최소 길이를 올려 저품질 청크를 없앤다"는 작업이, 재처리와 결합되는 순간
**저품질 청크를 오히려 검색 가능한 상태로 고착시키는** 결과가 된다.

**재처리는 반드시 이 순서로**:
1. `payload.parent_id == doc._id`인 point를 **전부 삭제**
2. 새 청크 upsert
3. `is_embedded=True`

이를 위해 `parent_id` payload 인덱스가 필요하다 (→ 4.5에 함께 반영).

### 4.5 payload 인덱스가 `keyword` 하나뿐이다

`DB/drant_clitent.py:48`은 `keyword`에만 인덱스를 만드는데, `rag_pipeline.py:44`는 `source`로도
필터링한다. 추가로 필요한 것:

- `source` — 이미 필터링에 쓰이고 있음
- `parent_id` — **재처리 시 삭제 대상 조회에 필수** (4.4의 함정)
- `is_relevant` — judge cherry-pick 시

`ensure_collection()`에 나란히 추가하면 된다 (`create_payload_index`는 재호출이 안전하다).

---

## 5. 단계별 현재 수준과 남은 개선점

### 5.1 크롤링 — 수준: 양호, 소스별 편차 있음

**갖춘 것**: 도메인별 RateLimiter(동시성과 요청 rate를 분리해 제어), UA 로테이션,
조용한 차단 감지(`is_blocked`, 본문 길이로 false positive 방지), 지수 백오프 재시도,
`(키워드×소스)` 평평한 스레드풀 병렬화, 4개 소스의 관련성 게이트.
크롤러 계층 자체는 이 프로젝트에서 가장 성숙한 부분이다.

**남은 것** (선행 문서에서 아직 미해결):

| 항목 | 내용 | 근거 |
|---|---|---|
| 1.2 네이버 블로그 미러링 | 같은 글이 `in.naver.com`/`blog.naver.com` 두 URL로 중복 수집. `_id`가 URL 해시라 중복 판정이 안 됨 | `tavily_crawler.py:24` |
| 1.3 리다이렉트/스니펫 URL | `url`이 `/goto?url=CAES...`이고 `title`이 "…"로 끝나는 항목 = 실제 페이지가 아니라 검색결과 카드 | 선행 문서 1.3 |
| 날짜 컷오프 미적용 소스 | `CRAWL_MAX_AGE_YEARS=3`이 natepann/dcinside에만 적용. tavily/youtube는 무제한 | `base.py:68` 호출부 |

> tavily 쿼리 재설계(1.1)는 **하지 말 것을 권한다.** 따옴표 구문 일치 + score 0.3으로 이미
> 오염 58%를 차단했고, 쿼리를 더 바꾸면 수집량 자체가 흔들려 회귀 측정이 어려워진다.
> judge를 머지해 하류에서 거르는 쪽이 비용 대비 효과가 훨씬 낫다.

### 5.2 청킹 — 수준: 가장 취약한 단계

**갖춘 것**: 나무위키 섹션 마커 기반 구조 청킹(마커 값을 config로 공유해 크롤러/청커 결합),
`[댓글]` 경계를 최우선 구분자로 둔 재귀 분할, HTML/URL/이메일/전화 정제,
밈 어감 보존을 위한 반복문자 "삭제 대신 축약".
**의도는 잘 잡혀 있는데 임계값과 예외 처리가 데이터를 못 따라간다.**

**남은 것**:

| 항목 | 내용 | 위치 |
|---|---|---|
| **2.1 최소 길이 10자** | `"야르\n- dc official App"`(13자)를 못 거름. RAG가 30자로 재차 거르는 이유 | `chunker.py:81` |
| **2.2 본문/댓글 분리 실패** | `RecursiveCharacterTextSplitter`는 전체가 `CHUNK_SIZE`(500) 미만이면 **아예 분할하지 않는다**. 짧은 본문+긴 스팸 댓글이 한 청크로 합쳐져, 실질 정보량은 한 단어인데 길이 필터를 통과 | `chunker.py:22-26`, `config_cilent.py:59` |
| 2.3 스팸 미필터 | `⭐ 라. 인: King 365 z ⭐` 류 대출/도박 스팸. 전화번호 정규식이 있어도 텔레그램 아이디로 우회 | `cleaner.py:33` |
| 2.4 JSON 조각 혼입 | `{"title":"...","source":"` 같은 JSON-LD 파편이 본문에 섞임 | `cleaner.py` |
| 2.5 UI 상투어 혼입 | "본문 바로가기", "이웃추가", "찬반대결 책갈피 최신순-추천순" | `cleaner.py` |

**2.2가 가장 중요하다.** 이건 임계값 튜닝이 아니라 로직 결함이다. 문서 전체 길이와 무관하게
`[댓글]` 마커에서 **항상** 먼저 쪼개고, 각 조각에 `content_type: body|comment`를 붙여야
길이 필터가 "본문 자체의 정보량"을 정확히 평가할 수 있다. 지금은 스팸 댓글이 길이를 부풀려
필터를 속이는 구조가 그대로 열려 있다.

### 5.3 임베딩 — 수준: 견고하지만 품질 신호가 없음

**갖춘 것**: BGE-M3 dense(1024)+sparse 동시 인코딩, 모델 지연 로드,
`uuid5(parent_id::chunk_index)` 결정론적 point id(재실행 시 안전한 upsert),
문서 단위 원자성(전 청크 upsert 성공 후에만 `is_embedded=True`), 명시적 VRAM 해제,
`ensure_collection()` 멱등 보장.
**메커니즘은 잘 짜여 있다. 문제는 "무엇을 넣을지 판단하는 층"이 없다는 것.**

**남은 것**:

| 항목 | 내용 |
|---|---|
| **3.3 적재 시점 keyword 검증** | judge cherry-pick으로 해결 (3절) |
| **3.1 품질 필드 payload화** | 같은 cherry-pick에 `is_relevant`/`relevance_position` 포함. `char_count`는 추가 검토 |
| 3.2 근접 중복 사전 제거 | 지금은 RAG가 매 질의마다 `difflib`로 처리. 적재 시점 콘텐츠 해시(SimHash/MinHash)로 한 번만 거르면 이후 모든 질의가 혜택 |
| 컨텍스트 없는 인코딩 | `_build_points`가 `c["text"]`만 인코딩한다(`embedding/pipeline.py:52`). 나무위키 섹션 청크는 `title`/`section_title`을 앞에 붙여 인코딩하면 문맥이 살아난다 (구현 비용 낮고 효과 확인 쉬움) |
| CHUNK_SIZE 재검토 | BGE-M3는 8192 토큰까지 받는데 500자로 쪼개고 있다. 밈 설명은 문맥이 중요하므로 800~1000자 실험 여지. 단 2.2 해결 후에 볼 것 |

---

## 6. 권장 실행 순서

기존 개선안의 순서를 **현재 코드 기준으로 재정렬**했다. 앞의 3개는 근거가 확실하고 비용이 낮다.

| # | 작업 | 근거 | 규모 |
|---|---|---|---|
| **1** | `feature/rag_test`에서 judge cherry-pick (`preprocessing/relevance.py` + pipeline 2곳 + `eval/`) | 개선안 3.1·3.3이 이미 구현·측정 완료. dev의 유령 주석 2곳이 이걸 전제로 함 | 파일 4개, 신규 코드 거의 없음 |
| **2** | 청커 본문/댓글 **항상** 분리 + `content_type` 필드 (2.2) | 임계값 문제가 아니라 로직 결함. 스팸이 길이 필터를 속이는 경로를 닫음 | `chunker.py` 중간 규모 |
| **3** | **재처리 경로 먼저 마련** — `parent_id`로 기존 point 삭제 후 재적재 (4.4) | 2·4번이 기존 데이터에 반영되려면 필수이고, **이게 없으면 4번이 역효과**를 낸다(고아 청크). 순서를 뒤집지 말 것 | 중간 |
| **4** | 청커 최소 길이 10 → 30, RAG와 일치 (2.1 + 4.2) | 한 줄 수정. 저장공간·임베딩 비용·질의 성능 동시 개선. **단 3번 완료 후에** | 1줄 (+ 짧은 키워드 예외 검토) |
| **5** | judge에 `late` 위치 가중치 적용 (3.3) | 라벨셋 실측: 오탐 8→4, 정상 1건만 손실. 단 라벨셋 100건으로 재측정 후 | 수 줄 |
| **6** | `normalize()` 여러 벌 → `relevance.normalize()` 1벌로 수렴 (4.1) | **정리 차원이 아니라 실제 버그 수정** — `거제 야호~` 문서가 지금 RAG에서 탈락 중. 급하면 `rag_pipeline.py:54`를 `\s+` 제거로 바꾸는 1줄 핫픽스가 먼저 가능 | 중간 (핫픽스는 1줄) |
| **7** | 스팸/UI 상투어/JSON 조각 제거 (2.3·2.4·2.5) | 데이터 품질 다듬기. 소스별 상투어 목록이 필요해 손이 좀 감 | 중간 |
| **8** | 네이버 미러링 + 근접 중복 사전 제거 (1.2·3.2) | 컨텍스트 낭비 방지. URL host 정규화는 싸고, 콘텐츠 해시는 조금 비쌈 | 중간 |
| **9** | 임베딩 시 title/section_title 프리펜딩 (5.3) | 나무위키 롱폼 검색 품질. 실험으로 효과 확인 후 결정 | 작음 |
| **10** | `source`/`is_relevant` payload 인덱스 (4.5) | 1번 이후 정리 차원 | 2줄 |

**하지 않기를 권하는 것**: tavily 쿼리 재설계(5.1 참고), `title` 필드 용도 변경(4.3 참고).

---

## 7. RAG 쪽에 생길 영향 (`analysis/rag_pipeline.py`)

현재 RAG는 상류의 부재를 메우려고 방어 필터 5종(최소 길이, 키워드 하드필터, dense 하한선,
소스당 상한, `difflib` 근접 중복)을 들고 있다. 위 작업이 진행되면:

- **1번 완료 시**: `_is_valid_chunk`의 키워드 체크는 payload `is_relevant` 조회로 대체 가능해진다
  (질의마다 재계산 → 저장된 필드 읽기). 다만 **의미는 미묘하게 다르다** — judge는 doc 단위,
  현재 RAG 필터는 청크 단위다. 바꾸면 "밈을 다루지만 그 청크엔 키워드가 없는" 청크가
  이제 통과하게 되는데, 이건 의도된 개선이다(judge 독스트링이 밝힌 설계 근거).
- **4번 완료 시**: RAG의 `min_length` 필터는 사실상 no-op이 된다. 해롭지 않으니 급히 지울 필요는 없다.
- **8번 완료 시**: `difflib` 근접 중복 제거의 부담이 줄어든다.

payload 필드명(`keyword`/`title`/`text`/`source`)을 바꾸는 리팩터링을 한다면 **반드시 RAG 쪽에도 알릴 것.**
`rag_pipeline.py`는 전부 `.get()`으로 접근해 에러 없이 빈 문자열을 읽고, 결과적으로 모든 청크가
길이 필터에 걸려 매번 백필만 발생하는 상태가 된다 — **조용히 망가진다.**

---

## 8. 검증 방법

작업 전후 비교는 `feature/rag_test`의 하네스를 쓰면 새로 만들 필요가 없다:

- `eval/relevance_eval.py` — judge 정밀도/재현율 측정 + Qdrant 백필
- `eval/run_compare.py` — RAG 검색 방식 비교(통계·민감도·latency)
- `eval/results/relevance_labelset.csv` — 사람 라벨 50건 (100건으로 확장 권장)

`analysis/langchain_playground.ipynb`와 `analysis/eval_runs.jsonl`은 이 하네스와 별개로
질의 단위 실험을 기록하고 있으므로, 파이프라인 개선 전후로 같은 질문 세트를 돌려
답변 품질 회귀를 확인하는 용도로 병행하면 좋다.

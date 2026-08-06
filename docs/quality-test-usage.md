# `quality_test` 사용법

> 작성 2026-08-03. 브랜치 `feature/data_update`.
> 설계: `docs/superpowers/specs/2026-08-03-pipeline-quality-instrumentation-design.md`
> 배경 조사: `docs/pipeline-current-state-2026-08-03.md`

파이프라인이 만들어내는 데이터의 품질을 **측정하고, 눈으로 보고, 개선 전후를 비교**하는 도구다.

Mongo를 읽는 건 `dump` 하나뿐이고 나머지는 전부 로컬 파일만 다룬다. EC2도 Qdrant도 안 건드린다.

---

## 0. 한 줄 요약

```bash
python quality_test_main.py dump    # 실데이터 스냅샷 뜨기 (1회)
python quality_test_main.py run     # 파이프라인 돌려서 측정
python quality_test_main.py report  # 숫자로 보기
python quality_test_main.py inspect # 나쁜 청크 실물 보기
```

---

## 1. 처음 한 번: fixture 뜨기

측정의 기준이 될 실제 문서를 로컬 파일로 떠 놓는다.

**PowerShell** (이 프로젝트의 기본 환경):

```powershell
$env:MONGODB_URI = 'mongodb://localhost:27017'
uv run python quality_test_main.py dump --limit 1000
Remove-Item Env:MONGODB_URI
```

**bash / Git Bash**:

```bash
MONGODB_URI=mongodb://localhost:27017 uv run python quality_test_main.py dump --limit 1000
```

> PowerShell에는 `VAR=값 명령` 형태의 인라인 환경변수 지정이 없다. 따로 설정하고, 쓰고, 지운다.
> `Remove-Item`을 빼먹으면 그 PowerShell 세션이 끝날 때까지 값이 남는다.

**`MONGODB_URI`를 덮어쓰는 이유**: `.env`의 값이 `mongo:27017`(도커 내부 호스트명)이라 호스트에서는 해석되지 않는다. `.env`를 고치면 도커 실행이 깨지므로 이 명령에서만 덮어쓴다.

옵션:
- `--limit N` — 최대 문서 수 (기본 1000)
- `--keyword K` — 특정 키워드 문서만

결과: `data_test/fixtures/raw_sample.jsonl`

**왜 fixture가 필요한가**: 크롤러가 계속 새 문서를 넣기 때문에 Mongo를 매번 읽으면 입력이 달라진다. 그러면 "개선 전후"를 비교해도 코드 변경 때문인지 데이터 변경 때문인지 구분할 수 없다. 한 번 떠 놓고 그 위에서만 비교한다.

---

## 2. 측정하기

```bash
uv run python quality_test_main.py run --name before
```

fixture를 정제→청킹 파이프라인에 통과시키고 각 청크의 품질 신호를 계산한다. **Mongo도 Qdrant도 안 건드린다.**

결과: `data_test/runs/before/`
- `cleaned.jsonl` — 문서 단위 정제 결과
- `chunks.jsonl` — 청크 + 품질 신호
- `funnel.json` — 단계별 입력/출력/드롭 수

옵션:
- `--name` (필수) — run 이름
- `--fixture` — 다른 fixture 경로 지정

> 정제/청킹은 `preprocessing.pipeline.process_one()`을 그대로 호출한다. 프로덕션이 쓰는 것과 **같은 함수**라 측정값과 실제 동작이 어긋날 수 없다.

---

## 3. 숫자로 보기

```bash
uv run python quality_test_main.py report before
```

```
=== before ===
  문서 (입력 → 통과)              1,000 → 999
    └ empty_content             1
  청크                        1,921
  청크 길이 중앙값                   323
  --- 확실히 나쁨 규칙 ---
  30자 미만                      168     8.7%
  스팸 3건 이상                      4     0.2%
  키워드 미포함                     465    24.2%
  본문 후반 매치(late)               29     1.5%
  UI 상투어 포함                   161     8.4%
  한글비율<0.3 (국내)                36     1.9%
  --- 소스별 청크 ---
  tavily                      829    43.2%
  ...
```

### 두 run 비교 — 이게 핵심 기능이다

```bash
uv run python quality_test_main.py report before after
```

```
  30자 미만                      168        0   -168
  청크 길이 중앙값                   323      412    +89
```

개선 작업의 효과가 델타로 바로 보인다.

---

## 4. 실물 보기

숫자만 보면 착각한다. 나쁜 청크를 직접 읽어야 원인을 안다.

```bash
uv run python quality_test_main.py inspect before --worst 10
```

```
[1] 위반 3건: 짧음(<30), 한글비율(0.292), UI상투어(1)
    source=dcinside  len=29  match=early(1회)
    title='이번 주만 끝나면..'
    text='에버랜드다 야르!!! - dc official App'
```

위반 규칙 개수 순으로 정렬한다. 합성 점수를 만들지 않고 **위반 목록을 그대로 보여주므로 왜 위에 올라왔는지가 설명된다.**

옵션:
- `--keyword K` — 특정 키워드만
- `--worst N` — 상위 N개 (기본 10)

---

## 5. 실제 작업 흐름

개선 작업을 할 때 이 순서로 돈다.

```
1. fixture 뜬다                     dump          (처음 한 번)
2. 현재 코드로 기준선 측정            run --name before
3. 숫자를 본다                      report before
4. 나쁜 것 실물을 본다               inspect before --worst 20
5. 코드를 고친다                     (청커/cleaner/judge 등)
6. 같은 fixture로 다시 측정          run --name after
7. 델타를 본다                      report before after
8. 좋아졌으면 커밋 → 배포
```

**3~7이 EC2 없이, Mongo 없이, 파일만으로 돈다.**

---

## 6. 판정 기준 바꾸기

임계값은 전부 `config/config_cilent.py`에 있다.

```python
MIN_CHUNK_CHARS = 30        # 이 미만이면 정보 없는 청크
MIN_HANGUL_RATIO = 0.3      # 국내 소스인데 이 미만이면 본문 추출 실패 의심
SPAM_HIT_THRESHOLD = 3      # 스팸 패턴이 이 개수 이상이면 광고
DOMESTIC_SOURCES = ("natepann", "dcinside", "namuwiki")
```

**신호 계산과 판정이 분리돼 있어서**, 임계값을 바꿔도 `run`을 다시 돌릴 필요가 없다. `report`/`inspect`만 다시 실행하면 저장된 신호에 새 기준이 적용된다.

---

## 7. 테스트

**PowerShell**:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
uv run python tests/test_matching.py
uv run python tests/test_signals.py
uv run python tests/test_funnel.py
uv run python tests/test_reembed.py
Remove-Item Env:PYTHONIOENCODING
```

**bash / Git Bash**:

```bash
PYTHONIOENCODING=utf-8 uv run python tests/test_matching.py
PYTHONIOENCODING=utf-8 uv run python tests/test_signals.py
PYTHONIOENCODING=utf-8 uv run python tests/test_funnel.py
PYTHONIOENCODING=utf-8 uv run python tests/test_reembed.py
```

각 파일 마지막에 `ALL PASS ✅`가 찍히면 통과다.

`PYTHONIOENCODING=utf-8`은 Windows 콘솔이 cp949라 한글 출력이 깨지는 걸 막기 위한 것이다. 전부 외부 연결 없이 돈다 — `test_reembed.py`는 인메모리 Qdrant를 쓰므로 서버도 도커도 필요 없다.

> `quality_test_main.py`는 실행 시 stdout을 UTF-8로 재설정하므로 `report`/`inspect` 등 CLI 명령에는 이 환경변수가 필요 없다.

---

## 8. 알아둘 것

**산출물은 커밋되지 않는다.** `data_test/`는 `.gitignore`에 있다. 용량이 크고(run당 5~10MB), 크롤링 원문이 들어 있고, 코드만 있으면 다시 만들 수 있기 때문이다.

**폴더 이름의 `_test`는 "검증 중"이라는 표시다.** 정식 채택하면 `quality_test/` → `quality/`로 바꾸고 import 8줄 정도를 함께 고치면 된다.

**"키워드 미포함 24.2%"를 오염률로 읽으면 안 된다.** 청크 단위 수치다. 관련 있는 문서라도 모든 청크가 키워드를 반복하지는 않는다. 문서 단위 판정은 2단계 judge가 한다.

**`quality_test/inspect.py`는 표준 라이브러리 `inspect`와 이름이 같지만 무해하다.** Python 3는 절대 import를 쓰므로 `import inspect`는 여전히 표준 라이브러리를 가리킨다.

---

## 9. 진행 상태 (2026-08-04 새벽 갱신)

이 도구는 **측정만** 한다. 데이터를 고치지는 않는다.

| | 상태 |
|---|---|
| 1단계: 측정 기반 + 재처리 안전성 | ✅ 완료 |
| 2단계: relevance judge (레벨 0, 리터럴 매칭) | ✅ 구현 완료 — `preprocessing/pipeline.py`의 `process_one()`이 문서 단위로 판정해 청크마다 `is_relevant`/`relevance_position`/`relevance_match_count`를 붙인다. `embedding/pipeline.py` 페이로드에도 반영됨. |
| 2단계: 정규화 완전 수렴(크롤러 ↔ matching.py) | 미착수 |
| 3단계: 본문/댓글 항상 분리(`content_type`), 청크 최소 길이 10→30 | ✅ 구현 완료 — `preprocessing/chunker.py` |
| 재처리 트리거 (`is_embedded` 리셋) | 없음 — 아직 아무도 안 만듦 |
| 라벨셋(사람 검증) 기반 재현율/정밀도 확인 | **미실행** — 아래 §10 참고 |
| EC2 배포, Mongo `pipeline_runs` 적재 | 안 함 |
| 커밋 | **아직 0건** — 사용자가 직접 커밋 예정 |

### 이번 변경으로 새로 생긴 청크 필드
- `is_relevant: bool`, `relevance_position: "title"|"early"|"late"|"none"`, `relevance_match_count: int` — 문서 단위로 동일하게 붙음(청크마다 다르지 않음).
- `content_type: "body"|"comment"` — `"[댓글]"` 마커 기준으로 항상 먼저 분리한 뒤 각각 독립적으로 청킹한 결과. 나무위키 섹션 청크는 전부 `"body"`.

`report`/`inspect`는 아직 이 필드들을 요약하지 않는다(설계에 없던 추가 기능이라 이번엔 손대지 않음). 문서 단위로 직접 보려면:

```python
import json, collections
docs = {}
with open("data_test/runs/<run_name>/chunks.jsonl", encoding="utf-8") as f:
    for line in f:
        c = json.loads(line)
        docs.setdefault(c["parent_id"], c["is_relevant"])
irrelevant = sum(1 for v in docs.values() if v is False)
print(f"{irrelevant}/{len(docs)} 문서가 judge 비관련 판정")
```

## 10. 오늘 밤 측정 결과와 한계 (반드시 읽을 것)

**측정 순서**: `before`(1단계까지) → judge만 추가해 `after_judge` → 청킹까지 추가해 `after_chunking`. 커밋 없이 순서대로 진행했으므로 `git stash` 없이도 판단은 정확히 격리된다.

### judge 단독 효과 (`after_judge`, 기존 6개 규칙 리포트에는 안 잡힘 — §9 스니펫으로 직접 집계)
- 문서 898건(청크가 1개 이상 있는 문서 기준) 중 **85건(9.5%)**을 비관련으로 판정.
- 소스별: tavily 59/298(19.8%), natepann 24/189(12.7%), dcinside 2/408(0.5%).
- **중요한 정정**: 예전 50건 라벨셋 기준으로 "오염은 natepann이 대부분"이라고 말한 적이 있는데, 그건 그 라벨셋이 낡아서(오염 사례 상당수가 지금은 크롤링 단계에서 이미 막힌 natepann 패턴)였다. 오늘 밤 실제 데이터에서는 **tavily가 잔여 오염의 대부분**이고, tavily/유튜브 유형("키워드는 있는데 주제가 다름")은 리터럴 매칭이 가장 못 잡는 유형이다. 즉 judge가 남은 오염의 "대부분"을 닫는다고 보면 안 된다.

### judge 구현 버그 발견 및 수정 (2026-08-04, 청킹 이후 표본 검증 중 발견)
사용자가 natepann/dcinside 비관련 판정 표본을 직접 요청해서 봤더니, tavily와 달리 **진짜 오탐(false negative)**이 섞여 있었다. 원인: judge가 "살아남은 청크만 이어붙인 텍스트"로 판정하고 있었는데, 청킹 필터(30자 미만 드롭)가 키워드가 든 짧은 본문 청크를 지워버리면 그 증거가 통째로 사라져 비관련으로 오판됐다(예: 본문 `"야호\n- dc official App"`(22자)는 드롭, 댓글 청크만 남아 키워드가 사라짐 — 원문 전체로 다시 판정하면 매치됨).

**수정**: `preprocessing/pipeline.py`의 `_attach_relevance()`가 청크 join 대신 `clean_content`(청킹 이전 정제 전문)로 판정하도록 변경. 청킹 임계값과 judge 판정이 서로 얽히지 않게 됨. 회귀 테스트 `tests/test_judge.py::test_키워드가_든_짧은_본문이_청킹필터에_걸려도_관련_판정된다` 추가.

**수정 후 재측정** (`after_chunking`):
```
문서 713건 중 judge 비관련: 78건(10.9%)
  tavily:   58/296 (19.6%)  — 그대로
  natepann: 18/138 (13.0%)  — 그대로
  dcinside:  2/276 (0.7%)   — 36건 → 2건으로 급감
```
dcinside가 극적으로 개선됐다(본문 짧고 댓글 긴 구조가 dcinside에 많았기 때문). natepann은 그대로인데, 이건 이 버그와 무관한 **다른 원인**이다 — natepann 표본을 읽어보니 키워드 자체가 "뭔말알" 같은 축약형인데 실제 게시글 제목/본문은 "뭔 말인지 알거 같음"처럼 풀어서 쓰는 경우가 많아, 리터럴 매칭이 애초에 못 잡는 유형이다(로드맵의 레벨2 "alias 테이블"이 필요한 지점 — 오늘 밤 범위 밖).

### 청킹 단독 효과 (`after_judge` → `after_chunking`)
```
청크                1,921 → 1,778   (-143)
청크 길이 중앙값       323 →   353   (+30)
30자 미만              168 →     0   (-168, 정확히 의도대로)
UI 상투어 포함         161 →   119   (-42)
한글비율<0.3(국내)      36 →    17   (-19)
키워드 미포함          465 →   523   (+58, 아래 참고)
```
- "키워드 미포함"이 늘어난 건 나빠진 게 아니라, 본문+댓글이 섞여 있을 때 본문 쪽 키워드가 댓글 청크에 잘못 묻어가던 게 분리로 사라졌을 가능성이 큼(더 정직한 신호가 됨) — 실제로 확인함(`"야한 건 안돼!... 야르!\n\n[댓글]\n왜 않대?..."` 같은 사례에서, 예전엔 통짜 청크라 "매치"였는데 분리 후 댓글 청크만 따로 보면 "미매치"가 정직하게 드러남).
- **문서가 통째로 사라지는 부작용 발견 → 사용자 판단으로 그대로 두기로 결정**: 청크가 1개라도 있는 문서 수가 898 → 713건으로 줄었다(185건이 완전히 사라짐). 표본을 30건으로 늘려 정식 `find_keyword`로 확인해보니 30/30 전부 자기 키워드를 포함하고 있었는데, 이는 크롤러가 애초에 그 키워드로 검색해서 모은 글이라 당연한 것(오염 여부의 근거가 안 됨). 진짜 쟁점은 "10~30자짜리 초단문이 분석 가치가 있는가"였고, 본문+댓글 합산 길이 기준(개별은 미달이어도 합치면 30자 넘으면 살리는 예외)을 추가할지 물어봤으나 **사용자가 "개별 30자 기준 그대로 유지"로 결정** — 코드 변경 없음.
- **judge 구현 버그 발견 및 수정** (위 "judge 구현 버그" 항목 참고): 이 부작용을 조사하다가 natepann/dcinside 판정 표본을 직접 읽어보고 진짜 버그(청킹 필터가 지운 텍스트 때문에 judge가 오판)를 발견해 수정함. 수정 후 `after_chunking` 위에서 재계산한 비관련 비율은 9.5%(898건 기준) → **10.9%**(78/713)로, 버그 수정 전 15.7%보다 훨씬 준수한 수치다. dcinside는 사실상 해소(0.5%→0.7%로 거의 그대로), natepann의 13%는 이 버그와 무관한 별도 원인(키워드 축약형 vs 실제 게시글의 풀어쓴 표현 — 로드맵 레벨2 alias 테이블 필요).

### 사람 라벨 검증은 오늘 밤 못 했다
1단계 검증 절차의 필수 게이트(라벨 100건으로 재현율/정밀도 확인)는 사람이 직접 라벨을 달아야 해서 자동으로 못 돌렸다. 위 숫자들은 전부 "델타가 어느 방향으로 움직였는지"이지 "그 방향이 실제로 좋은지"의 확인이 아니다. `feature/rag_test`의 기존 50건 라벨셋은 이미 낡았다고 판정된 상태([[project_unmerged_relevance_judge]] 메모리 참고)라 그대로 못 쓴다.

### 다음에 사람이 볼 것
1. `inspect after_chunking --worst 20` 으로 여전히 나쁜 청크가 뭔지 실물 확인.
2. §9 스니펫으로 `after_chunking`의 judge 비관련 85~112건 중 일부를 직접 읽어 오탐(진짜 관련 있는데 비관련으로 잘못 판정) 여부 확인.
3. 위 "문서 통째 드롭" 185건 중 몇 건을 더 열어 (b)/(c) 중 뭘 선택할지 결정.
4. 전부 괜찮으면 커밋 — 지금까지처럼 사용자가 직접 진행.

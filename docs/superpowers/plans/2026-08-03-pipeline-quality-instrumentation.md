# 파이프라인 품질 계측 (1단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 파이프라인 각 단계의 데이터 품질을 측정·조회할 수 있게 하고, 재처리 시 고아 청크가 생기는 결함과 정규화 드리프트 버그를 고친다.

**Architecture:** 새 패키지 `quality_test/`가 품질 신호 계산과 측정 도구를 담당한다. DB를 만지는 것은 `fixture.py` 한 파일뿐이고 나머지는 파일과 문자열만 다룬다. 측정이 프로덕션과 다른 코드를 재는 것을 막기 위해, `preprocessing/pipeline.py`에서 DB를 안 만지는 알맹이를 `process_one()`으로 뽑아 Mongo 경로와 fixture 경로가 함께 쓴다.

**Tech Stack:** Python 3, `qdrant-client` 1.18.0 (인메모리 모드), `pymongo`, 표준 라이브러리(`re`, `json`, `unicodedata`, `dataclasses`, `argparse`). 새 의존성 없음.

## Global Constraints

- **브랜치**: `feature/data_update`. 작업 전 확인할 것.
- **새 의존성 금지**: pytest를 포함해 어떤 패키지도 추가하지 않는다. 표준 라이브러리와 이미 설치된 것만 쓴다.
- **테스트 관례**: 이 프로젝트에는 pytest가 없다. `tests/test_crawl_parallel.py`와 같은 형태 — 순수 파이썬 스크립트, `assert` + `print("[OK] ...")`, 파일 하단 `if __name__ == "__main__":` 러너, 마지막에 `print("\nALL PASS ✅")`. 실행은 `uv run python tests/test_x.py`.
- **테스트 파일 상단 필수 줄** (프로젝트 루트 import용):
  ```python
  import os, sys
  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
  ```
- **판정 로직 불변**: 1단계는 크롤러/청커가 무엇을 거르는지 바꾸지 않는다. 계측 추가, delete 추가, 정규화 버그 수정만 한다. 유일한 예외는 Task 2(정규화 수정)로, RAG 검색 결과가 의도적으로 바뀐다.
- **기본 동작 보존**: `.env`를 안 건드리면 컬렉션 이름이 현재와 같아야 한다 (`memes`, `cleaned_memes`, `mimori_chunks`).
- **커밋**: 이 프로젝트는 **커밋 전 사용자 확인이 필요하다**. 각 Task의 커밋 단계에서 명령을 제시하되, 실행 전 사용자에게 물어볼 것.
- **인코딩**: Windows 콘솔이 cp949라 한글/이모지 출력이 깨질 수 있다. 테스트 실행 시 `PYTHONIOENCODING=utf-8`을 붙이거나, 스크립트 상단에서 stdout을 재설정할 것.
- **예상되는 경고 두 가지** (둘 다 무해하며 실패가 아니다):
  - `DB/drant_clitent.py`를 import하면 모듈 최상단에서 `QdrantClient(host, port)`가 만들어지면서 `UserWarning: Failed to obtain server version`이 뜬다. 서버가 안 떠 있어도 예외는 안 나므로 그대로 진행하면 된다.
  - `embedding/pipeline.py`를 import하면 `encoder.py`가 torch와 FlagEmbedding을 끌어와 **약 11초**가 걸린다. 모델 로드는 지연되므로 GPU는 필요 없다. `tests/test_reembed.py` 시작이 느린 것은 정상이다.
- **경로**: 모든 산출물 경로는 `config/config_cilent.py`의 `_ROOT` 기준 절대경로로 만든다. cwd 기준 상대경로 금지.

**참조 스펙:** `docs/superpowers/specs/2026-08-03-pipeline-quality-instrumentation-design.md`

---

## File Structure

**생성**

| 파일 | 책임 |
|---|---|
| `quality_test/__init__.py` | 패키지 선언 (빈 파일) |
| `quality_test/matching.py` | 키워드↔텍스트 매칭의 유일한 정의. 순수 함수 |
| `quality_test/signals.py` | 청크 하나의 품질 신호 계산. 순수 함수. `matching`에 단방향 의존 |
| `quality_test/funnel.py` | 단계별 카운터 누적과 직렬화. 순수 함수 |
| `quality_test/fixture.py` | Mongo ↔ JSONL. **DB를 만지는 유일한 파일** |
| `quality_test/runner.py` | fixture로 파이프라인 실행 → run 디렉터리 산출 |
| `quality_test/report.py` | run 집계·비교 출력 |
| `quality_test/inspect.py` | run 개별 청크 조회 |
| `quality_test_main.py` | CLI. `dump` / `run` / `report` / `inspect` |
| `tests/test_matching.py` | 개행 회귀 테스트 포함 |
| `tests/test_signals.py` | 스팸·상투어·한글비율 |
| `tests/test_funnel.py` | 카운터 누적·직렬화 |
| `tests/test_reembed.py` | 인메모리 Qdrant 고아 청크 |

**수정**

| 파일 | 변경 | Task |
|---|---|---|
| `analysis/rag_pipeline.py` | `_normalize_for_match` → `matching.normalize` | 2 |
| `config/config_cilent.py` | 컬렉션 env override, 경로, 품질 임계값 | 4 |
| `.gitignore` | `data_test/` 추가 | 4 |
| `preprocessing/pipeline.py` | `process_one()` 추출 | 6 |
| `DB/drant_clitent.py` | `parent_id`·`source` payload 인덱스 | 8 |
| `embedding/pipeline.py` | upsert 전 `parent_id` filter delete | 8 |

---

## Task 1: 키워드 매칭 단일 정의

**Files:**
- Create: `quality_test/__init__.py`
- Create: `quality_test/matching.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: 없음 (첫 Task)
- Produces:
  - `normalize(s: str) -> str`
  - `Match` dataclass — `.matched: bool`, `.position: str`, `.count: int`
  - `find_keyword(keyword: str, title: str, body: str) -> Match`
  - `EARLY_BODY_CHARS: int = 200`

**배경:** 현재 `normalize`가 5곳에 있고 동작이 3종류다. 크롤러(`natepann_crawler.py:43`)는 `\s`로 개행까지 지우는데 RAG(`analysis/rag_pipeline.py:54`)는 스페이스만 지운다. 크롤러가 `get_text(separator="\n")`로 본문을 뽑으므로 키워드 `거제 야호~`가 텍스트에 `거제\n야호`로 들어오는 일이 흔하고, 그러면 **크롤러는 통과시키는데 RAG는 탈락시킨다.** 에러도 로그도 안 남는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_matching.py`:

```python
"""
키워드 매칭 단일 정의 검증 (외부 의존 없음, 1초 미만).

핵심은 개행 케이스다. 크롤러가 get_text(separator="\n")로 본문을 뽑기 때문에
"거제 야호~" 같은 공백 포함 키워드가 텍스트에서는 "거제\n야호"로 쪼개져 들어온다.
이걸 매칭하지 못하면 정상 수집된 문서가 검색에서 조용히 탈락한다.

실행:  uv run python tests/test_matching.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quality_test.matching import find_keyword, normalize


def test_개행으로_쪼개진_키워드도_매칭된다():
    # 크롤러가 인라인 요소 경계마다 개행을 넣어 실제로 이 형태가 만들어진다
    m = find_keyword("거제 야호~", title="", body="미나미가 외치는 거제\n야호 에서 야호는")
    assert m.matched, "개행이 낀 키워드가 매칭되지 않음 — 이게 지금의 버그"
    assert m.position == "early", m.position
    print("[OK] 개행으로 쪼개진 키워드 매칭")


def test_물결_개수가_달라도_매칭된다():
    # 키워드는 "좋~다~", 실제 게시글은 "좋다~~~" 처럼 물결 개수가 다르다
    m = find_keyword("좋~다~", title="", body="오늘 진짜 좋다~~~ 기분 최고")
    assert m.matched, "물결 개수 차이로 매칭 실패"
    print("[OK] 물결 개수 차이 흡수")


def test_제목_매치가_우선한다():
    m = find_keyword("야르", title="야르의 뜻", body="관련 없는 본문 내용")
    assert m.matched and m.position == "title", (m.matched, m.position)
    print("[OK] 제목 매치 우선")


def test_본문_후반_매치는_late다():
    body = "가" * 300 + "야르" + "나" * 10
    m = find_keyword("야르", title="", body=body)
    assert m.matched and m.position == "late", (m.matched, m.position)
    print("[OK] 본문 후반 매치는 late")


def test_어디에도_없으면_none이다():
    m = find_keyword("야르", title="여행 후기", body="괌 자유여행 정보입니다")
    assert not m.matched and m.position == "none" and m.count == 0, m
    print("[OK] 미매치는 none")


def test_등장_횟수를_센다():
    m = find_keyword("야르", title="야르", body="야르 야르 하는 야르")
    assert m.count == 4, m.count  # 제목 1 + 본문 3
    print("[OK] 등장 횟수 집계")


def test_빈_키워드는_매치되지_않는다():
    m = find_keyword("", title="아무거나", body="아무거나")
    assert not m.matched and m.count == 0, m
    print("[OK] 빈 키워드 방어")


def test_정규화가_대소문자와_공백류를_없앤다():
    assert normalize("  Hello\tWorld\n") == "helloworld"
    assert normalize("거제 야호~") == "거제야호"
    print("[OK] 정규화 규칙")


if __name__ == "__main__":
    test_개행으로_쪼개진_키워드도_매칭된다()
    test_물결_개수가_달라도_매칭된다()
    test_제목_매치가_우선한다()
    test_본문_후반_매치는_late다()
    test_어디에도_없으면_none이다()
    test_등장_횟수를_센다()
    test_빈_키워드는_매치되지_않는다()
    test_정규화가_대소문자와_공백류를_없앤다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run python tests/test_matching.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'quality_test'`

- [ ] **Step 3: 패키지와 구현을 만든다**

`quality_test/__init__.py` — 빈 파일로 생성.

`quality_test/matching.py`:

```python
"""
matching.py
키워드 ↔ 텍스트 매칭의 유일한 정의.

왜 한 곳에 모으나:
  크롤러(_normalize), RAG(_normalize_for_match), 미머지 judge가 각자 다른 정규화를
  갖고 있었고, 그 차이 때문에 "크롤러는 통과시켰는데 RAG는 탈락시키는" 문서가 실제로
  생겼다. 크롤러는 \\s(개행 포함)를 지우는데 RAG는 스페이스만 지웠기 때문이다 —
  크롤러가 get_text(separator="\\n")로 본문을 뽑으므로 "거제 야호~"가 텍스트에서는
  "거제\\n야호"로 들어오는 일이 흔하다.

  정의가 하나면 이런 드리프트가 구조적으로 불가능해진다.

부분 매칭을 하지 않는 이유:
  "거제야호"를 "야호"로 부분매칭해 10년 전 여행 후기를 끌어온 오염 사례가 있었다.
  정규화 후 '전체 키워드'가 통으로 등장해야만 매치로 인정한다.

position을 기록하는 이유:
  긴 SEO 문서가 다른 밈을 나열하다 본문 후반에 키워드를 한 번 흘리는 경우가 있다
  (주제는 여전히 무관). 지금은 가중치를 두지 않고 위치만 남겨, 나중에 라벨셋으로
  규칙을 정할 수 있게 한다.
"""

import re
import unicodedata
from dataclasses import dataclass

# 본문 '초반'으로 볼 정규화 후 문자 수. position이 early/late로 갈리는 경계.
EARLY_BODY_CHARS = 200

# 정규화 때 제거할 장식 문자(물결류). 키워드에 '~'가 들어있다: "거제 야호~", "좋~다~".
_DECORATIVE = "~〜﹏∼"
_DECORATIVE_TABLE = {ord(c): None for c in _DECORATIVE}
_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """매칭용 정규화: NFC → casefold → 공백류 전부 제거 → 장식 물결 제거.

    공백류는 \\s로 지운다 — 스페이스뿐 아니라 개행/탭까지 포함해야 한다.
    크롤러가 넣은 개행 때문에 매칭이 깨지는 것이 이 함수가 존재하는 이유다.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.casefold()
    s = _WS_RE.sub("", s)
    return s.translate(_DECORATIVE_TABLE)


@dataclass(frozen=True)
class Match:
    """매칭 결과. matched=False면 position="none", count=0."""
    matched: bool
    position: str  # "title" | "early" | "late" | "none"
    count: int


def find_keyword(keyword: str, title: str, body: str) -> Match:
    """title+body에서 keyword를 찾아 (매치 여부, 위치, 등장 횟수)를 반환.

    제목을 먼저 본다(가장 강한 신호). 제목에 없으면 본문에서 첫 매치 위치로
    early/late를 가른다.
    """
    kw = normalize(keyword)
    if not kw:
        return Match(False, "none", 0)

    title_norm = normalize(title)
    body_norm = normalize(body)
    count = title_norm.count(kw) + body_norm.count(kw)

    if kw in title_norm:
        return Match(True, "title", count)

    idx = body_norm.find(kw)
    if idx == -1:
        return Match(False, "none", 0)
    return Match(True, "early" if idx < EARLY_BODY_CHARS else "late", count)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_matching.py`
Expected: PASS — `[OK]` 8줄 후 `ALL PASS ✅`

- [ ] **Step 5: 커밋 (사용자 확인 후)**

```bash
git add quality_test/__init__.py quality_test/matching.py tests/test_matching.py
git commit -m "feat(quality): 키워드 매칭 단일 정의 + 개행 회귀 테스트"
```

---

## Task 2: RAG 정규화 버그 수정

**Files:**
- Modify: `analysis/rag_pipeline.py:54-79`

**Interfaces:**
- Consumes: Task 1의 `quality_test.matching.normalize`
- Produces: 없음 (기존 `_is_valid_chunk` 동작만 교정)

**배경:** `analysis/rag_pipeline.py:54`의 `_DECORATIVE_CHARS = " ~"`는 스페이스와 물결만 지운다. 개행이 남아 `거제 야호~` 문서가 지금 검색에서 탈락 중이다. Task 1의 `normalize`로 교체한다.

**이 Task는 RAG 검색 결과를 바꾼다.** 1단계에서 유일하게 판정이 달라지는 지점이며, 버그 수정이므로 의도된 변화다.

- [ ] **Step 1: 현재 코드를 읽고 교체 지점을 확인한다**

Run: `grep -n "_DECORATIVE_CHARS\|_normalize_for_match\|_is_valid_chunk" analysis/rag_pipeline.py`
Expected: 54~79행 근처에 세 심볼이 보인다.

- [ ] **Step 2: 버그 재현을 확인한다**

Run:
```bash
PYTHONIOENCODING=utf-8 uv run python -c "
import sys; sys.path.insert(0, '.')
from analysis.rag_pipeline import _normalize_for_match as old
from quality_test.matching import normalize as new
kw, text = '거제 야호~', '외치는 거제\n야호 에서'
print('현재 구현 통과:', old(kw) in old(text))
print('새 구현 통과 :', new(kw) in new(text))
"
```
Expected: `현재 구현 통과: False` / `새 구현 통과 : True`

`Failed to obtain server version` 경고가 같이 뜨는데 무해하다 (Global Constraints 참고).

- [ ] **Step 3: 교체한다**

`analysis/rag_pipeline.py`에서 `_DECORATIVE_CHARS`와 `_normalize_for_match` 정의(54~60행)를 **삭제**하고, 파일 상단 import 블록에 다음을 추가한다:

```python
from quality_test.matching import normalize as _normalize_for_match
```

`_is_valid_chunk`의 본문은 그대로 두되, 독스트링의 설명을 현실에 맞게 고친다:

```python
def _is_valid_chunk(point: models.ScoredPoint, keyword: str, min_length: int) -> bool:
    """크롤러가 매긴 keyword 태그를 그대로 믿지 않고, title/text에 실제로 그 키워드가
    있는지 + 텍스트가 최소 길이 이상인지 재검증한다.

    정규화는 quality_test.matching.normalize 하나만 쓴다 — 예전에는 이 파일이
    스페이스와 물결만 지우는 자체 구현을 갖고 있었고, 크롤러(\\s로 개행까지 제거)와
    규칙이 어긋나 "거제 야호~"처럼 공백이 든 키워드의 문서가 조용히 탈락했다.
    """
    text = point.payload.get("text", "")
    title = point.payload.get("title", "")
    if len(text.strip()) < min_length:
        return False
    kw_norm = _normalize_for_match(keyword)
    combined = _normalize_for_match(text + title)
    return kw_norm in combined
```

- [ ] **Step 4: import가 깨지지 않는지 확인한다**

Run: `uv run python -c "import sys; sys.path.insert(0,'.'); import analysis.rag_pipeline; print('import OK')"`
Expected: `import OK`

- [ ] **Step 5: 매칭 테스트를 다시 돌린다 (회귀 확인)**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_matching.py`
Expected: `ALL PASS ✅`

- [ ] **Step 6: 커밋 (사용자 확인 후)**

```bash
git add analysis/rag_pipeline.py
git commit -m "fix(rag): 정규화가 개행을 무시해 공백 포함 키워드 문서가 탈락하던 버그"
```

---

## Task 3: 청크 품질 신호 계산

**Files:**
- Create: `quality_test/signals.py`
- Test: `tests/test_signals.py`

**Interfaces:**
- Consumes: Task 1의 `find_keyword`
- Produces:
  - `hangul_ratio(text: str) -> float`
  - `spam_hits(text: str) -> int`
  - `boilerplate_hits(text: str) -> int`
  - `compute_signals(text: str, title: str, keyword: str) -> dict` — 키: `char_count`, `hangul_ratio`, `spam_hits`, `boilerplate_hits`, `match_position`, `match_count`

**설계 원칙:** 값만 계산하고 좋고 나쁨은 판단하지 않는다. 임계값은 데이터를 보고 정하는 것이라 계속 바뀌는데, 신호와 판정이 섞이면 임계값을 바꿀 때마다 데이터를 다시 계산해야 한다. 나눠두면 저장된 신호에 새 임계값을 적용하는 게 공짜다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_signals.py`:

```python
"""
청크 품질 신호 계산 검증 (외부 의존 없음, 1초 미만).

스팸 패턴은 실제 수집 데이터에서 관찰된 것을 그대로 샘플로 쓴다. 중요한 건
'정상 한국어 문장에는 걸리지 않는 것'이다 — 특히 마침표가 들어간 평범한 문장.

실행:  uv run python tests/test_signals.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quality_test.signals import (
    boilerplate_hits,
    compute_signals,
    hangul_ratio,
    spam_hits,
)

# 실제 수집된 대출 스팸 댓글 (natepann "퇴근버스" 게시글에서 관찰)
REAL_SPAM = "⭐ 라. 인: King 365 z ⭐ 사/고-자 신/불/자 연/체/자 가능"


def test_실제_스팸을_잡는다():
    assert spam_hits(REAL_SPAM) >= 3, spam_hits(REAL_SPAM)
    print(f"[OK] 실제 스팸 검출: {spam_hits(REAL_SPAM)}건")


def test_정상_한국어_문장은_스팸이_아니다():
    samples = [
        "오늘 진짜 재밌었습니다. 그리고 내일도 갈 거예요",
        "야르~ 90년도에 유행하던거 아님?ㅋㅋ 꼰대만 아는 유행어인데",
        "이 단어가 한국 온라인 커뮤니티로 넘어오면서 감탄사로 변형된 것이죠.",
    ]
    for s in samples:
        assert spam_hits(s) == 0, f"오탐: {s!r} → {spam_hits(s)}"
    print("[OK] 정상 문장 오탐 없음 (마침표 포함 문장 포함)")


def test_UI_상투어를_잡는다():
    text = "본문 바로가기 이웃추가 공유하기 URL복사 신고하기 실제 내용은 여기부터"
    assert boilerplate_hits(text) >= 4, boilerplate_hits(text)
    assert boilerplate_hits("평범한 게시글 본문입니다") == 0
    print(f"[OK] UI 상투어 검출: {boilerplate_hits(text)}건")


def test_한글비율을_잰다():
    assert hangul_ratio("한글만있음") == 1.0
    assert hangul_ratio("abcdef") == 0.0
    # "한글abc" = 공백 제외 5자 중 한글 2자 → 0.4
    assert abs(hangul_ratio("한글abc") - 0.4) < 0.01, hangul_ratio("한글abc")
    assert hangul_ratio("") == 0.0
    print("[OK] 한글 비율 계산")


def test_공백은_한글비율_분모에서_빠진다():
    # 공백을 세면 "짧은 한글 + 많은 공백"이 낮은 비율로 잘못 나온다
    assert hangul_ratio("한 글") == 1.0, hangul_ratio("한 글")
    print("[OK] 공백은 분모 제외")


def test_신호_묶음을_한번에_낸다():
    sig = compute_signals(text="야르 하는 중", title="야르 후기", keyword="야르")
    assert sig["char_count"] == len("야르 하는 중"), sig["char_count"]
    assert sig["match_position"] == "title", sig["match_position"]
    assert sig["match_count"] == 2, sig["match_count"]
    assert sig["spam_hits"] == 0 and sig["boilerplate_hits"] == 0, sig
    assert 0.0 <= sig["hangul_ratio"] <= 1.0, sig["hangul_ratio"]
    print("[OK] compute_signals 묶음 출력")


def test_스팸_청크의_신호가_전부_나쁘게_나온다():
    sig = compute_signals(text=f"야르\n\n[댓글]\n{REAL_SPAM}", title="퇴근버스", keyword="야르")
    assert sig["spam_hits"] >= 3, sig
    assert sig["match_position"] == "early", sig["match_position"]
    assert sig["match_count"] == 1, sig["match_count"]
    print("[OK] 스팸 청크 신호 조합")


if __name__ == "__main__":
    test_실제_스팸을_잡는다()
    test_정상_한국어_문장은_스팸이_아니다()
    test_UI_상투어를_잡는다()
    test_한글비율을_잰다()
    test_공백은_한글비율_분모에서_빠진다()
    test_신호_묶음을_한번에_낸다()
    test_스팸_청크의_신호가_전부_나쁘게_나온다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run python tests/test_signals.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'quality_test.signals'`

- [ ] **Step 3: 구현을 만든다**

`quality_test/signals.py`:

```python
"""
signals.py
청크 하나의 품질 신호를 계산한다.

값만 내고 좋고 나쁨은 판단하지 않는다 — 임계값은 데이터를 보고 정하는 것이라
계속 바뀌는데, 신호와 판정이 섞이면 임계값을 바꿀 때마다 데이터를 다시 계산해야 한다.
나눠두면 저장된 신호에 새 임계값을 적용해보는 것이 공짜다.
판정(임계값 적용)은 report.py / inspect.py가 config 상수를 읽어서 한다.

청크 하나만 보면 전부 계산되는 순수 함수다. 문서 간 비교가 필요한 근접 중복은
성격이 달라 여기 없다(2단계).
"""

import re

from quality_test.matching import find_keyword

_HANGUL_RE = re.compile(r"[가-힣]")
_NON_SPACE_RE = re.compile(r"\S")

# 대출/도박 스팸이 전화번호 정규식을 피하려고 쓰는 표기들.
# 실제 수집 데이터에서 관찰: "⭐ 라. 인: King 365 z ⭐", "사/고-자 신/불/자 연/체/자"
#
# _SPAM_SPLIT_RE: 한 글자씩 구분자로 쪼갠 패턴이 2회 이상 연속.
#   "사/고-자" → 사/ 고- 두 번 → 매치. 정상 한국어 문장은 이 형태가 안 나온다
#   ("습니다. 그리고"는 '다.' 한 번뿐이라 {2,}에 미달).
_SPAM_SPLIT_RE = re.compile(r"(?:[가-힣][/\-.]){2,}")
_SPAM_DECO_RE = re.compile(r"[⭐★☆✩✪◆◇▶►♠♣]")
_SPAM_SLANG_RE = re.compile(r"대출|급전|신불자|연체자|사채|카톡|텔레그램|여기로")

# 소스별 UI 상투어. 본문 추출이 사이드바/위젯까지 긁어왔을 때 나타난다.
_BOILERPLATE_PHRASES = (
    "본문 바로가기", "메뉴 바로가기", "마이페이지",
    "이웃추가", "구독하기", "공유하기", "URL복사", "신고하기",
    "찬반대결", "책갈피", "최신순", "추천순",
    "dc official App",
)


def hangul_ratio(text: str) -> float:
    """공백을 제외한 문자 중 한글 음절의 비율.

    공백을 분모에 넣으면 "짧은 한글 + 많은 공백"이 낮게 나와, 본문 추출 실패와
    구분이 안 된다. 국내 소스인데 이 값이 낮으면 추출 실패나 영어 SEO를 의심한다.
    """
    if not text:
        return 0.0
    non_space = len(_NON_SPACE_RE.findall(text))
    if non_space == 0:
        return 0.0
    return len(_HANGUL_RE.findall(text)) / non_space


def spam_hits(text: str) -> int:
    """대출/도박 스팸 특유의 패턴이 몇 번 나타나는지."""
    if not text:
        return 0
    return (
        len(_SPAM_SPLIT_RE.findall(text))
        + len(_SPAM_DECO_RE.findall(text))
        + len(_SPAM_SLANG_RE.findall(text))
    )


def boilerplate_hits(text: str) -> int:
    """사이트 UI 상투어가 몇 개 섞여 있는지."""
    if not text:
        return 0
    return sum(1 for phrase in _BOILERPLATE_PHRASES if phrase in text)


def compute_signals(text: str, title: str, keyword: str) -> dict:
    """청크 하나의 품질 신호를 한 번에 계산해 dict로 반환.

    반환 키는 chunks.jsonl의 "signals" 필드에 그대로 들어간다.
    """
    match = find_keyword(keyword or "", title or "", text or "")
    return {
        "char_count": len(text or ""),
        "hangul_ratio": round(hangul_ratio(text or ""), 3),
        "spam_hits": spam_hits(text or ""),
        "boilerplate_hits": boilerplate_hits(text or ""),
        "match_position": match.position,
        "match_count": match.count,
    }
```

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_signals.py`
Expected: PASS — `ALL PASS ✅`

- [ ] **Step 5: 커밋 (사용자 확인 후)**

```bash
git add quality_test/signals.py tests/test_signals.py
git commit -m "feat(quality): 청크 품질 신호 계산 (길이/한글비율/스팸/상투어/매치)"
```

---

## Task 4: 설정·경로 정비와 fixture 덤프

**Files:**
- Modify: `config/config_cilent.py`
- Modify: `.gitignore`
- Create: `quality_test/fixture.py`
- Create: `quality_test_main.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - config: `DATA_TEST_DIR`, `FIXTURE_DIR`, `RUNS_DIR`, `MIN_CHUNK_CHARS`, `MIN_HANGUL_RATIO`, `SPAM_HIT_THRESHOLD`, `DOMESTIC_SOURCES`, `DEFAULT_FIXTURE_NAME`
  - `fixture.dump_fixture(limit: int, keyword: str | None = None, path: str | None = None) -> tuple[str, int]` — (저장경로, 문서수)
  - `fixture.load_fixture(path: str) -> Iterator[dict]`
  - CLI: `python quality_test_main.py dump [--limit N] [--keyword K]`

- [ ] **Step 1: config에 경로와 임계값을 추가한다**

`config/config_cilent.py`의 컬렉션 정의부(11~16행)를 env override로 바꾼다:

```python
# MongoDB
MONGO_URI = os.getenv("MONGODB_URI", "")
MONGO_DB = "mimori"
# 컬렉션 이름은 env로 덮어쓸 수 있다 — 테스트용 컬렉션(memes_test 등)으로 돌릴 때 쓴다.
# 기본값이 현재와 같으므로 .env를 안 건드리면 동작이 동일하다.
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "memes")
CLEANED_COLLECTION = os.getenv("CLEANED_COLLECTION", "cleaned_memes")
TREND_COLLECTION = "trend_scores"
```

`QDRANT_COLLECTION`(68행)도 같은 방식으로:

```python
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "mimori_chunks")
```

파일 끝에 품질 계측 블록을 추가한다:

```python
# ── 품질 계측 (quality_test) ────────────────────────────────────────────────
# 산출물 경로. cwd가 아니라 프로젝트 루트 기준으로 고정한다 —
# 다른 폴더에서 실행해도 같은 곳에 쌓이게 하기 위함.
DATA_TEST_DIR = os.path.join(_ROOT, "data_test")
FIXTURE_DIR = os.path.join(DATA_TEST_DIR, "fixtures")
RUNS_DIR = os.path.join(DATA_TEST_DIR, "runs")
DEFAULT_FIXTURE_NAME = "raw_sample.jsonl"

# 품질 판정 임계값. signals.py는 값만 계산하고, 판정은 이 상수를 읽는 쪽에서 한다.
# 전부 '확실히 나쁜 것만' 잡도록 보수적으로 잡은 시작값이며, 리포트로 분포를 보고 조정한다.
MIN_CHUNK_CHARS = 30        # 이 미만이면 정보 없는 청크로 본다 (RAG 필터와 같은 값)
MIN_HANGUL_RATIO = 0.3      # 국내 소스인데 이 미만이면 본문 추출 실패 의심
SPAM_HIT_THRESHOLD = 3      # 스팸 패턴이 이 개수 이상이면 광고로 본다
DOMESTIC_SOURCES = ("natepann", "dcinside", "namuwiki")  # 한글 비율 규칙을 적용할 소스
```

- [ ] **Step 2: 기본 동작이 안 바뀐 것을 확인한다**

Run:
```bash
uv run python -c "import sys; sys.path.insert(0,'.'); from config.config_cilent import MONGO_COLLECTION, CLEANED_COLLECTION, QDRANT_COLLECTION; print(MONGO_COLLECTION, CLEANED_COLLECTION, QDRANT_COLLECTION)"
```
Expected: `memes cleaned_memes mimori_chunks`

- [ ] **Step 3: .gitignore에 산출물 경로를 추가한다**

`.gitignore`의 `data/` 줄 바로 아래에 추가:

```
data_test/
```

Run: `git check-ignore -v data_test/` (디렉터리가 아직 없으면 `git check-ignore -v data_test/x.jsonl`)
Expected: `.gitignore:N:data_test/	data_test/...`

- [ ] **Step 4: fixture 모듈을 만든다**

`quality_test/fixture.py`:

```python
"""
fixture.py
실제 수집 문서를 로컬 JSONL로 떠놓고 다시 읽는다.

이 패키지에서 DB를 만지는 유일한 파일이다. 나머지 모듈은 전부 파일과 문자열만
다루므로 EC2/Mongo 없이, 목(mock) 없이 테스트된다.

왜 fixture가 필요한가:
  크롤러가 계속 새 문서를 넣기 때문에 Mongo를 직접 읽으면 실행할 때마다 입력이
  달라진다. 그러면 "개선 전후"를 비교해도 무엇이 코드 변경 때문이고 무엇이 데이터
  변경 때문인지 구분할 수 없다. 한 번 떠놓고 그 위에서만 비교한다.

읽기만 한다. 이 파일은 절대 Mongo에 쓰지 않는다.
"""

import json
import os
from typing import Iterator

from config.config_cilent import DEFAULT_FIXTURE_NAME, FIXTURE_DIR
from DB.mongo_client import get_collection


def dump_fixture(limit: int, keyword: str | None = None, path: str | None = None) -> tuple[str, int]:
    """memes 컬렉션에서 문서를 읽어 JSONL로 저장. (저장경로, 문서수) 반환.

    datetime 등 JSON으로 직렬화되지 않는 값은 default=str로 문자열화한다 —
    fixture는 정제/청킹 입력으로만 쓰이고 그 단계는 content/title/keyword만 보므로
    날짜 타입이 문자열이 되어도 무해하다.
    """
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    path = path or os.path.join(FIXTURE_DIR, DEFAULT_FIXTURE_NAME)

    query = {}
    if keyword:
        query["keyword"] = keyword

    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for doc in get_collection().find(query).limit(limit):
            f.write(json.dumps(doc, ensure_ascii=False, default=str) + "\n")
            count += 1

    return path, count


def load_fixture(path: str | None = None) -> Iterator[dict]:
    """JSONL을 한 줄씩 읽어 문서 dict를 내놓는다.

    한 줄씩 읽는 이유: 파일이 커져도 메모리에 전부 올리지 않는다.
    """
    path = path or os.path.join(FIXTURE_DIR, DEFAULT_FIXTURE_NAME)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
```

- [ ] **Step 5: CLI 진입점을 만든다**

`quality_test_main.py`:

```python
"""
quality_test_main.py
파이프라인 품질 계측 CLI.

  dump     실제 문서를 로컬 JSONL fixture로 뜬다 (Mongo 읽기, 1회)
  run      fixture로 파이프라인을 돌려 run 디렉터리를 만든다 (파일만)
  report   run을 집계해 표로 보여준다 / 두 run을 비교한다
  inspect  run에서 나쁜 청크 실물을 보여준다

dump 외에는 외부 연결이 전혀 없다.
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def cmd_dump(args):
    from quality_test.fixture import dump_fixture

    path, count = dump_fixture(limit=args.limit, keyword=args.keyword)
    print(f"[dump] {count}개 문서 → {path}")
    if count == 0:
        print("[dump] 경고: 0건이다. Mongo 연결이나 --keyword 값을 확인할 것.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quality_test_main.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_dump = sub.add_parser("dump", help="실제 문서를 JSONL fixture로 뜬다")
    p_dump.add_argument("--limit", type=int, default=1000, help="최대 문서 수 (기본 1000)")
    p_dump.add_argument("--keyword", default=None, help="이 키워드 문서만 (기본: 전체)")
    p_dump.set_defaults(func=cmd_dump)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
```

- [ ] **Step 6: CLI가 뜨는지 확인한다 (DB 연결 없이)**

Run: `uv run python quality_test_main.py --help`
Expected: `dump` 서브커맨드가 보이는 도움말 출력

- [ ] **Step 7: 실제 fixture를 뜬다**

Run: `uv run python quality_test_main.py dump --limit 1000`
Expected: `[dump] N개 문서 → .../data_test/fixtures/raw_sample.jsonl` (N > 0)

Mongo 연결이 안 되면 여기서 멈추고 `.env`의 `MONGODB_URI`를 확인한다. 이후 Task들은 이 파일이 있어야 진행된다.

- [ ] **Step 8: 커밋 (사용자 확인 후)**

```bash
git add config/config_cilent.py .gitignore quality_test/fixture.py quality_test_main.py
git commit -m "feat(quality): 컬렉션 env override + 품질 임계값 + fixture 덤프"
```

`data_test/`는 gitignore되므로 커밋에 안 들어간다. `git status`로 확인할 것.

---

## Task 5: 단계별 카운터

**Files:**
- Create: `quality_test/funnel.py`
- Test: `tests/test_funnel.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `Funnel(run: str, fixture: str)` 클래스
  - `.inc_in(stage: str, n: int = 1) -> None`
  - `.inc_out(stage: str, n: int = 1) -> None`
  - `.drop(stage: str, reason: str, n: int = 1) -> None`
  - `.to_dict() -> dict`
  - `.dump(path: str) -> None`
  - `load_funnel(path: str) -> dict`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_funnel.py`:

```python
"""
단계별 카운터 검증 (외부 의존 없음, 1초 미만).

실행:  uv run python tests/test_funnel.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quality_test.funnel import Funnel, load_funnel


def test_카운터가_누적된다():
    f = Funnel("before", "raw_sample.jsonl")
    f.inc_in("clean", 10)
    f.inc_out("clean", 8)
    f.drop("clean", "empty_content", 2)
    d = f.to_dict()
    assert d["stages"]["clean"]["in"] == 10, d
    assert d["stages"]["clean"]["out"] == 8, d
    assert d["stages"]["clean"]["dropped"]["empty_content"] == 2, d
    print("[OK] 카운터 누적")


def test_드롭_사유가_따로_집계된다():
    f = Funnel("r", "x.jsonl")
    f.drop("chunk", "too_short")
    f.drop("chunk", "too_short")
    f.drop("chunk", "no_keyword")
    dropped = f.to_dict()["stages"]["chunk"]["dropped"]
    assert dropped == {"too_short": 2, "no_keyword": 1}, dropped
    print("[OK] 사유별 집계")


def test_처음_보는_단계도_자동으로_생긴다():
    f = Funnel("r", "x.jsonl")
    f.inc_out("embed", 5)
    stage = f.to_dict()["stages"]["embed"]
    assert stage == {"in": 0, "out": 5, "dropped": {}}, stage
    print("[OK] 단계 자동 생성")


def test_run과_fixture_이름이_기록된다():
    d = Funnel("after", "sample.jsonl").to_dict()
    assert d["run"] == "after" and d["fixture"] == "sample.jsonl", d
    print("[OK] 메타 기록")


def test_저장하고_다시_읽으면_같다():
    f = Funnel("before", "raw.jsonl")
    f.inc_in("clean", 3)
    f.drop("clean", "empty_content")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "funnel.json")
        f.dump(path)
        assert load_funnel(path) == f.to_dict()
        # 사람이 읽을 수 있는 형태인지도 확인 (한글이 \uXXXX로 깨지지 않아야 함)
        with open(path, encoding="utf-8") as fp:
            raw = fp.read()
        assert "empty_content" in raw, raw
    print("[OK] 저장/로드 왕복")


def test_dump이_없는_디렉터리를_만든다():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nested", "deep", "funnel.json")
        Funnel("r", "x.jsonl").dump(path)
        assert os.path.exists(path)
    print("[OK] 상위 디렉터리 자동 생성")


if __name__ == "__main__":
    test_카운터가_누적된다()
    test_드롭_사유가_따로_집계된다()
    test_처음_보는_단계도_자동으로_생긴다()
    test_run과_fixture_이름이_기록된다()
    test_저장하고_다시_읽으면_같다()
    test_dump이_없는_디렉터리를_만든다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run python tests/test_funnel.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'quality_test.funnel'`

- [ ] **Step 3: 구현을 만든다**

`quality_test/funnel.py`:

```python
"""
funnel.py
파이프라인 단계별 입력/출력/사유별 드롭 수를 누적하고 직렬화한다.

지금은 각 단계가 print로 요약을 뱉고 끝나서, 어제 숫자를 알 방법도 없고
"몇 개를 왜 버렸는지"도 남지 않는다. 그래서 "고쳤더니 나아졌나"에 답할 수 없다.

만드는 dict는 하나이고 쓰는 곳만 다르다 — 개발 중에는 파일(dump), 운영에서는
Mongo(추후). 로직이 갈라지지 않으므로 "로컬에선 되는데 서버에선 다른 숫자" 사고가 없다.

청커 내부 드롭(chunker.py:81의 10자 미만 제거)은 여기 안 잡힌다. chunk_document()가
개수를 반환하지 않기 때문이며, 시그니처 변경은 1단계 범위 밖이다. 대신 살아남은
청크의 신호 분포(report.py)로 본다.
"""

import json
import os


class Funnel:
    """단계 이름 → {in, out, dropped:{사유: 수}} 누적기."""

    def __init__(self, run: str, fixture: str):
        self.run = run
        self.fixture = fixture
        self._stages: dict[str, dict] = {}

    def _stage(self, name: str) -> dict:
        return self._stages.setdefault(name, {"in": 0, "out": 0, "dropped": {}})

    def inc_in(self, stage: str, n: int = 1) -> None:
        self._stage(stage)["in"] += n

    def inc_out(self, stage: str, n: int = 1) -> None:
        self._stage(stage)["out"] += n

    def drop(self, stage: str, reason: str, n: int = 1) -> None:
        dropped = self._stage(stage)["dropped"]
        dropped[reason] = dropped.get(reason, 0) + n

    def to_dict(self) -> dict:
        return {"run": self.run, "fixture": self.fixture, "stages": self._stages}

    def dump(self, path: str) -> None:
        """JSON으로 저장. 상위 디렉터리가 없으면 만든다."""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


def load_funnel(path: str) -> dict:
    """dump()로 저장한 파일을 읽어 dict로 반환."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_funnel.py`
Expected: PASS — `ALL PASS ✅`

- [ ] **Step 5: 커밋 (사용자 확인 후)**

```bash
git add quality_test/funnel.py tests/test_funnel.py
git commit -m "feat(quality): 단계별 funnel 카운터"
```

---

## Task 6: 프로덕션 코드 공유 + 측정 실행

**Files:**
- Modify: `preprocessing/pipeline.py`
- Create: `quality_test/runner.py`
- Modify: `quality_test_main.py`

**Interfaces:**
- Consumes: Task 3 `compute_signals`, Task 4 `load_fixture`·config 경로, Task 5 `Funnel`
- Produces:
  - `preprocessing.pipeline.process_one(doc: dict) -> tuple[str, list[dict]]`
  - `runner.run(run_name: str, fixture_path: str | None = None) -> str` — run 디렉터리 경로 반환
  - CLI: `python quality_test_main.py run --name before`

**핵심:** `runner.py`가 정제·청킹을 자체 구현하면 **측정한 것과 실제로 도는 것이 다른 코드**가 된다. 시간이 지나면 반드시 어긋나고 리포트가 거짓말을 시작한다. `preprocess_documents()`에서 DB를 안 만지는 알맹이를 뽑아 양쪽이 같은 함수를 쓰게 한다.

- [ ] **Step 1: `process_one()`을 추출한다**

`preprocessing/pipeline.py`의 import 아래에 함수를 추가한다:

```python
def process_one(doc: dict) -> tuple[str, list[dict]]:
    """문서 하나를 정제 + 청킹한다. DB 접촉 없음.

    Mongo 경로(preprocess_documents)와 fixture 경로(quality_test/runner.py)가
    이 함수를 함께 쓴다 — 측정 대상이 프로덕션 코드와 다른 것이 되지 않도록
    한쪽만 고쳐질 수 없게 만든 장치다.

    반환: (clean_content, chunks)
    """
    clean_content = clean_text(doc.get("content", ""))
    return clean_content, chunk_document({**doc, "clean_content": clean_content})
```

`preprocess_documents()` 안의 해당 부분(44~48행)을 이 함수 호출로 바꾼다:

```python
        clean_content, chunks = process_one(doc)
```

`doc_for_chunking` 지역변수는 더 이상 필요 없으므로 지운다. 나머지(output_doc 조립, replace_one)는 그대로 둔다.

- [ ] **Step 2: 기존 파이프라인 import가 깨지지 않는지 확인한다**

Run: `uv run python -c "import sys; sys.path.insert(0,'.'); from preprocessing.pipeline import process_one, preprocess_documents; print('import OK')"`
Expected: `import OK`

- [ ] **Step 3: `process_one`이 실제로 동작하는지 확인한다 (DB 없이)**

Run:
```bash
PYTHONIOENCODING=utf-8 uv run python -c "
import sys; sys.path.insert(0, '.')
from preprocessing.pipeline import process_one
clean, chunks = process_one({'content': '야르 하는 중입니다. ' * 20, 'source': 'natepann', 'keyword': '야르', 'title': 'ㅇㅇ', '_id': 'x'})
print('clean 길이:', len(clean), '/ 청크 수:', len(chunks))
assert len(chunks) >= 1
"
```
Expected: `clean 길이: N / 청크 수: M` (M >= 1)

- [ ] **Step 4: runner를 만든다**

`quality_test/runner.py`:

```python
"""
runner.py
fixture로 파이프라인을 돌려 run 디렉터리를 만든다.

Mongo도 Qdrant도 건드리지 않는다 — 파일을 읽어 파일을 쓴다.
정제/청킹은 preprocessing.pipeline.process_one()을 그대로 호출하므로,
측정한 것과 프로덕션이 도는 코드가 항상 같다.

산출물:
  <RUNS_DIR>/<run_name>/cleaned.jsonl   문서 단위 정제 결과
  <RUNS_DIR>/<run_name>/chunks.jsonl    청크 + 품질 신호
  <RUNS_DIR>/<run_name>/funnel.json     단계별 카운터
"""

import json
import os

from config.config_cilent import RUNS_DIR
from preprocessing.pipeline import process_one
from quality_test.fixture import load_fixture
from quality_test.funnel import Funnel
from quality_test.signals import compute_signals


def run(run_name: str, fixture_path: str | None = None) -> str:
    """fixture를 처리해 run 디렉터리를 만들고 그 경로를 반환."""
    out_dir = os.path.join(RUNS_DIR, run_name)
    os.makedirs(out_dir, exist_ok=True)

    fixture_label = os.path.basename(fixture_path) if fixture_path else "raw_sample.jsonl"
    funnel = Funnel(run_name, fixture_label)

    cleaned_path = os.path.join(out_dir, "cleaned.jsonl")
    chunks_path = os.path.join(out_dir, "chunks.jsonl")

    with open(cleaned_path, "w", encoding="utf-8") as cf, \
         open(chunks_path, "w", encoding="utf-8") as chf:

        for doc in load_fixture(fixture_path):
            funnel.inc_in("clean")

            clean_content, chunks = process_one(doc)

            if not clean_content.strip():
                funnel.drop("clean", "empty_content")
                continue
            funnel.inc_out("clean")

            cf.write(json.dumps({
                "_id": doc.get("_id"),
                "keyword": doc.get("keyword"),
                "source": doc.get("source"),
                "url": doc.get("url"),
                "title": doc.get("title"),
                "clean_content": clean_content,
                "chunk_count": len(chunks),
            }, ensure_ascii=False, default=str) + "\n")

            funnel.inc_in("chunk")
            for chunk in chunks:
                chunk["signals"] = compute_signals(
                    text=chunk.get("text", ""),
                    title=chunk.get("title") or "",
                    keyword=chunk.get("keyword") or "",
                )
                chf.write(json.dumps(chunk, ensure_ascii=False, default=str) + "\n")
                funnel.inc_out("chunk")

    funnel.dump(os.path.join(out_dir, "funnel.json"))
    return out_dir
```

- [ ] **Step 5: CLI에 run 서브커맨드를 붙인다**

`quality_test_main.py`에 함수를 추가한다:

```python
def cmd_run(args):
    from quality_test.runner import run

    out_dir = run(run_name=args.name, fixture_path=args.fixture)
    print(f"[run] 완료 → {out_dir}")
```

`build_parser()`의 `return parser` 앞에 추가한다:

```python
    p_run = sub.add_parser("run", help="fixture로 파이프라인을 돌려 run을 만든다")
    p_run.add_argument("--name", required=True, help="run 이름 (예: before, after)")
    p_run.add_argument("--fixture", default=None, help="fixture 경로 (기본: 기본 fixture)")
    p_run.set_defaults(func=cmd_run)
```

- [ ] **Step 6: 실제로 돌린다**

Run: `uv run python quality_test_main.py run --name before`
Expected: `[run] 완료 → .../data_test/runs/before`

- [ ] **Step 7: 산출물 3개가 생겼는지 확인한다**

Run:
```bash
ls data_test/runs/before/
PYTHONIOENCODING=utf-8 uv run python -c "
import json
print(json.dumps(json.load(open('data_test/runs/before/funnel.json', encoding='utf-8')), ensure_ascii=False, indent=2))
first = open('data_test/runs/before/chunks.jsonl', encoding='utf-8').readline()
print('첫 청크 signals:', json.loads(first)['signals'])
"
```
Expected: `cleaned.jsonl chunks.jsonl funnel.json` 세 파일, funnel의 `clean`/`chunk` 카운트가 0보다 큼, 첫 청크에 `signals` 6개 키가 있음

- [ ] **Step 8: 커밋 (사용자 확인 후)**

```bash
git add preprocessing/pipeline.py quality_test/runner.py quality_test_main.py
git commit -m "feat(quality): process_one 추출로 측정/프로덕션 코드 공유 + run 실행"
```

---

## Task 7: 리포트와 인스펙터

**Files:**
- Create: `quality_test/report.py`
- Create: `quality_test/inspect.py`
- Modify: `quality_test_main.py`

**Interfaces:**
- Consumes: Task 4 config 임계값·`RUNS_DIR`, Task 5 `load_funnel`
- Produces:
  - `report.summarize(run_name: str) -> dict`
  - `report.print_report(run_a: str, run_b: str | None = None) -> None`
  - `inspect.violations(signals: dict, source: str) -> list[str]`
  - `inspect.print_worst(run_name: str, keyword: str | None, n: int) -> None`
  - CLI: `report <run> [<run_b>]`, `inspect <run> [--keyword K] [--worst N]`

**여기까지 하면 1단계의 목적(데이터를 볼 수 있게)이 달성된다.**

- [ ] **Step 1: report를 만든다**

`quality_test/report.py`:

```python
"""
report.py
run을 집계해 표로 보여주고, 두 run을 나란히 비교한다.

라벨 없이도 볼 수 있는 것만 다룬다 — 통과율, 드롭 사유, 신호 분포.
"좋다"는 못 말해도 "어제보다 나빠졌다"는 확실히 말한다.

청커 내부 드롭은 funnel에 없으므로(chunk_document가 개수를 안 돌려줌),
살아남은 청크의 signals를 집계해서 본다. 3단계(최소 길이 10→30)에서 필요한
숫자가 정확히 이것이다.
"""

import json
import os

from config.config_cilent import (
    DOMESTIC_SOURCES,
    MIN_CHUNK_CHARS,
    MIN_HANGUL_RATIO,
    RUNS_DIR,
    SPAM_HIT_THRESHOLD,
)
from quality_test.funnel import load_funnel


def _iter_chunks(run_name: str):
    path = os.path.join(RUNS_DIR, run_name, "chunks.jsonl")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def summarize(run_name: str) -> dict:
    """run 하나를 집계해 지표 dict로 반환."""
    funnel = load_funnel(os.path.join(RUNS_DIR, run_name, "funnel.json"))

    total = 0
    lengths = []
    short = spam = no_keyword = late = low_hangul = 0
    per_source: dict[str, int] = {}

    for chunk in _iter_chunks(run_name):
        sig = chunk.get("signals", {})
        source = chunk.get("source") or "unknown"
        total += 1
        lengths.append(sig.get("char_count", 0))
        per_source[source] = per_source.get(source, 0) + 1

        if sig.get("char_count", 0) < MIN_CHUNK_CHARS:
            short += 1
        if sig.get("spam_hits", 0) >= SPAM_HIT_THRESHOLD:
            spam += 1
        if sig.get("match_position") == "none":
            no_keyword += 1
        if sig.get("match_position") == "late":
            late += 1
        if source in DOMESTIC_SOURCES and sig.get("hangul_ratio", 1.0) < MIN_HANGUL_RATIO:
            low_hangul += 1

    return {
        "run": run_name,
        "docs_in": funnel["stages"].get("clean", {}).get("in", 0),
        "docs_out": funnel["stages"].get("clean", {}).get("out", 0),
        "docs_dropped": funnel["stages"].get("clean", {}).get("dropped", {}),
        "chunks": total,
        "median_length": _median(lengths),
        "short": short,
        "spam": spam,
        "no_keyword": no_keyword,
        "late": late,
        "low_hangul": low_hangul,
        "per_source": per_source,
    }


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):5.1f}%" if total else "    -"


def _row(label: str, a, b, total_a: int, total_b: int | None) -> str:
    """b가 None이면 단일 run 표시, 아니면 비교 표시."""
    if b is None:
        return f"  {label:<22} {a:>8,} {_pct(a, total_a):>8}"
    delta = b - a
    sign = "+" if delta > 0 else ""
    return f"  {label:<22} {a:>8,} {b:>8,}  {sign}{delta:>7,}"


def print_report(run_a: str, run_b: str | None = None) -> None:
    """run 하나를 표로 출력하거나, 두 run을 비교 출력한다."""
    a = summarize(run_a)
    b = summarize(run_b) if run_b else None

    if b is None:
        print(f"\n=== {a['run']} ===")
        print(f"  {'문서 (입력 → 통과)':<22} {a['docs_in']:>8,} → {a['docs_out']:,}")
        if a["docs_dropped"]:
            for reason, n in a["docs_dropped"].items():
                print(f"    └ {reason:<18} {n:>8,}")
        print(f"  {'청크':<22} {a['chunks']:>8,}")
        print(f"  {'청크 길이 중앙값':<22} {a['median_length']:>8,}")
        print("  --- 확실히 나쁨 규칙 ---")
        for label, key in (
            (f"{MIN_CHUNK_CHARS}자 미만", "short"),
            (f"스팸 {SPAM_HIT_THRESHOLD}건 이상", "spam"),
            ("키워드 미포함", "no_keyword"),
            ("본문 후반 매치(late)", "late"),
            (f"한글비율<{MIN_HANGUL_RATIO} (국내)", "low_hangul"),
        ):
            print(_row(label, a[key], None, a["chunks"], None))
        print("  --- 소스별 청크 ---")
        for source, n in sorted(a["per_source"].items(), key=lambda kv: -kv[1]):
            print(f"  {source:<22} {n:>8,} {_pct(n, a['chunks']):>8}")
    else:
        print(f"\n=== {a['run']} vs {b['run']} ===")
        print(f"  {'':<22} {a['run']:>8} {b['run']:>8}  {'Δ':>8}")
        print(_row("문서 통과", a["docs_out"], b["docs_out"], a["chunks"], b["chunks"]))
        print(_row("청크", a["chunks"], b["chunks"], a["chunks"], b["chunks"]))
        print(_row("청크 길이 중앙값", a["median_length"], b["median_length"], 0, 0))
        for label, key in (
            (f"{MIN_CHUNK_CHARS}자 미만", "short"),
            (f"스팸 {SPAM_HIT_THRESHOLD}건 이상", "spam"),
            ("키워드 미포함", "no_keyword"),
            ("본문 후반 매치(late)", "late"),
            (f"한글비율<{MIN_HANGUL_RATIO} (국내)", "low_hangul"),
        ):
            print(_row(label, a[key], b[key], a["chunks"], b["chunks"]))
    print()
```

- [ ] **Step 2: inspect를 만든다**

`quality_test/inspect.py`:

```python
"""
inspect.py
run에서 나쁜 청크 실물을 보여준다.

숫자만 보면 착각한다. 상위 N개를 눈으로 읽어봐야 "왜 나쁜지"를 알고 다음에
뭘 고칠지 정할 수 있다.

정렬 기준은 '위반한 규칙 개수'다. 점수를 합성하지 않고 위반 목록을 그대로
보여주므로, 왜 위에 올라왔는지가 설명된다.
"""

import json
import os

from config.config_cilent import (
    DOMESTIC_SOURCES,
    MIN_CHUNK_CHARS,
    MIN_HANGUL_RATIO,
    RUNS_DIR,
    SPAM_HIT_THRESHOLD,
)


def violations(signals: dict, source: str) -> list[str]:
    """이 청크가 위반한 '확실히 나쁨' 규칙 이름 목록."""
    found = []
    if signals.get("char_count", 0) < MIN_CHUNK_CHARS:
        found.append(f"짧음(<{MIN_CHUNK_CHARS})")
    if signals.get("match_position") == "none":
        found.append("키워드없음")
    if signals.get("spam_hits", 0) >= SPAM_HIT_THRESHOLD:
        found.append(f"스팸({signals['spam_hits']})")
    if source in DOMESTIC_SOURCES and signals.get("hangul_ratio", 1.0) < MIN_HANGUL_RATIO:
        found.append(f"한글비율({signals.get('hangul_ratio')})")
    if signals.get("match_position") == "late":
        found.append("후반매치")
    if signals.get("boilerplate_hits", 0) > 0:
        found.append(f"UI상투어({signals['boilerplate_hits']})")
    return found


def print_worst(run_name: str, keyword: str | None = None, n: int = 10) -> None:
    """위반 규칙이 많은 순으로 상위 n개 청크를 출력."""
    path = os.path.join(RUNS_DIR, run_name, "chunks.jsonl")

    scored = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            if keyword and chunk.get("keyword") != keyword:
                continue
            found = violations(chunk.get("signals", {}), chunk.get("source") or "")
            if found:
                scored.append((len(found), found, chunk))

    scored.sort(key=lambda item: -item[0])

    label = f"{run_name}" + (f" / keyword={keyword}" if keyword else "")
    print(f"\n=== 나쁜 청크 상위 {n}개 — {label} (위반 있는 청크 {len(scored):,}개) ===")
    for rank, (count, found, chunk) in enumerate(scored[:n], 1):
        sig = chunk.get("signals", {})
        print(f"\n[{rank}] 위반 {count}건: {', '.join(found)}")
        print(f"    source={chunk.get('source')}  len={sig.get('char_count')}  "
              f"match={sig.get('match_position')}({sig.get('match_count')}회)")
        print(f"    title={(chunk.get('title') or '')[:50]!r}")
        preview = (chunk.get("text") or "").replace("\n", " ")[:150]
        print(f"    text={preview!r}")
    print()
```

- [ ] **Step 3: CLI에 두 서브커맨드를 붙인다**

`quality_test_main.py`에 함수를 추가한다:

```python
def cmd_report(args):
    from quality_test.report import print_report

    print_report(args.run, args.compare_to)


def cmd_inspect(args):
    from quality_test.inspect import print_worst

    print_worst(run_name=args.run, keyword=args.keyword, n=args.worst)
```

`build_parser()`의 `return parser` 앞에 추가한다:

```python
    p_report = sub.add_parser("report", help="run을 집계하거나 두 run을 비교한다")
    p_report.add_argument("run", help="run 이름")
    p_report.add_argument("compare_to", nargs="?", default=None, help="비교할 run (선택)")
    p_report.set_defaults(func=cmd_report)

    p_inspect = sub.add_parser("inspect", help="나쁜 청크 실물을 본다")
    p_inspect.add_argument("run", help="run 이름")
    p_inspect.add_argument("--keyword", default=None, help="이 키워드만")
    p_inspect.add_argument("--worst", type=int, default=10, help="상위 N개 (기본 10)")
    p_inspect.set_defaults(func=cmd_inspect)
```

- [ ] **Step 4: 기준선 리포트를 뽑는다**

Run: `uv run python quality_test_main.py report before`
Expected: 문서/청크 수, 길이 중앙값, 규칙별 위반 수, 소스별 비중이 출력된다. **이 숫자가 기준선이다.**

- [ ] **Step 5: 나쁜 청크 실물을 확인한다**

Run: `uv run python quality_test_main.py inspect before --worst 10`
Expected: 위반 규칙 개수 순으로 청크 10개가 위반 목록·신호·본문 미리보기와 함께 출력된다.

- [ ] **Step 6: 비교 모드가 동작하는지 확인한다**

Run:
```bash
uv run python quality_test_main.py run --name before_copy
uv run python quality_test_main.py report before before_copy
```
Expected: 두 열이 나오고 모든 Δ가 0 (같은 fixture, 같은 코드이므로)

- [ ] **Step 7: 커밋 (사용자 확인 후)**

```bash
git add quality_test/report.py quality_test/inspect.py quality_test_main.py
git commit -m "feat(quality): 리포트/인스펙터 — 기준선 측정 가능"
```

---

## Task 8: 재처리 안전성 (고아 청크 차단)

**Files:**
- Modify: `DB/drant_clitent.py:24-52`
- Modify: `embedding/pipeline.py`
- Test: `tests/test_reembed.py`

**Interfaces:**
- Consumes: 없음
- Produces: `embedding.pipeline._delete_existing_points(parent_id: str) -> None`

**배경:** `embedding/pipeline.py`에 delete가 한 줄도 없고 point id가 `uuid5(parent_id::chunk_index)`로 결정론적이다. 청크 수가 줄어드는 재처리를 하면 뒤쪽 인덱스의 옛 point가 잔류하고, 그 point는 `keyword` payload를 멀쩡히 갖고 있어 RAG 검색에 계속 나온다. **"저품질 청크 제거" 작업이 오히려 저품질 청크를 고착시킨다.**

3단계(청커 최소 길이 10→30)가 정확히 청크 수를 줄이는 변경이므로, 그 전에 반드시 들어가 있어야 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_reembed.py`:

```python
"""
재적재 시 고아 청크가 남지 않는지 검증.

QdrantClient(":memory:")를 쓴다 — 서버도, 포트도, 도커도, EC2도 필요 없다.
프로젝트가 실제로 쓰는 기능(dense+sparse 네임드 벡터, uuid5 결정론적 id,
필터 delete)이 인메모리 모드에서 전부 동작함을 확인했다.

실행:  uv run python tests/test_reembed.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.http import models

COLL = "test_chunks"
NS = uuid.NAMESPACE_DNS


def _point_id(parent_id: str, idx: int) -> str:
    # embedding/pipeline.py:_point_id 와 같은 규칙
    return str(uuid.uuid5(NS, f"{parent_id}::{idx}"))


def _make_client() -> QdrantClient:
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=COLL,
        vectors_config={"dense": models.VectorParams(size=4, distance=models.Distance.COSINE)},
        sparse_vectors_config={"sparse": models.SparseVectorParams()},
    )
    return client


def _points(parent_id: str, n: int, tag: str):
    return [
        models.PointStruct(
            id=_point_id(parent_id, i),
            vector={
                "dense": [0.1 * (i + 1), 0.2, 0.3, 0.4],
                "sparse": models.SparseVector(indices=[i, i + 10], values=[0.9, 0.5]),
            },
            payload={"parent_id": parent_id, "chunk_index": i, "text": f"{tag} 청크 {i}"},
        )
        for i in range(n)
    ]


def _texts(client) -> list[str]:
    points, _ = client.scroll(COLL, limit=100, with_payload=True)
    return sorted(p.payload["text"] for p in points)


def test_delete_없이_재적재하면_고아가_남는다():
    """수정 전 동작을 고정해둔다 — 이 문제가 실재함을 증명하는 테스트."""
    client = _make_client()
    client.upsert(collection_name=COLL, points=_points("docA", 8, "구버전"))
    client.upsert(collection_name=COLL, points=_points("docA", 5, "신버전"))

    assert client.count(COLL).count == 8, "고아가 안 생겼다면 전제가 틀린 것"
    assert any("구버전" in t for t in _texts(client)), _texts(client)
    print("[OK] delete 없으면 고아 3개 잔류 (5개만 넣었는데 count=8)")


def test_delete_후_재적재하면_고아가_없다():
    from embedding.pipeline import _build_delete_filter

    client = _make_client()
    client.upsert(collection_name=COLL, points=_points("docA", 8, "구버전"))

    client.delete(
        collection_name=COLL,
        points_selector=models.FilterSelector(filter=_build_delete_filter("docA")),
    )
    client.upsert(collection_name=COLL, points=_points("docA", 5, "신버전"))

    assert client.count(COLL).count == 5, client.count(COLL).count
    assert not any("구버전" in t for t in _texts(client)), _texts(client)
    print("[OK] delete 후 재적재하면 고아 없음 (count=5)")


def test_다른_문서는_지워지지_않는다():
    from embedding.pipeline import _build_delete_filter

    client = _make_client()
    client.upsert(collection_name=COLL, points=_points("docA", 3, "A"))
    client.upsert(collection_name=COLL, points=_points("docB", 3, "B"))

    client.delete(
        collection_name=COLL,
        points_selector=models.FilterSelector(filter=_build_delete_filter("docA")),
    )

    remaining = _texts(client)
    assert len(remaining) == 3, remaining
    assert all(t.startswith("B") for t in remaining), remaining
    print("[OK] 필터가 대상 문서만 지운다")


if __name__ == "__main__":
    test_delete_없이_재적재하면_고아가_남는다()
    test_delete_후_재적재하면_고아가_없다()
    test_다른_문서는_지워지지_않는다()
    print("\nALL PASS ✅")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run python tests/test_reembed.py`
Expected: 첫 테스트는 PASS(고아 재현), 두 번째에서 FAIL — `ImportError: cannot import name '_build_delete_filter'`

두 번째 테스트가 `embedding.pipeline`을 import하므로 여기서 **약 11초** 멈춘다(torch 로드). 정상이다.
첫 테스트가 PASS하는 것이 중요하다 — 고아 청크 문제가 실재한다는 증거이며, 이게 FAIL이면
전제가 틀린 것이므로 멈추고 원인을 확인할 것.

- [ ] **Step 3: payload 인덱스를 추가한다**

`DB/drant_clitent.py`의 `ensure_collection()` 마지막 `create_payload_index` 호출을 다음으로 교체한다:

```python
    # 필터에 쓰이는 payload 필드는 전부 인덱스를 만들어 둔다.
    #   keyword   : RAG 검색 필터
    #   source    : RAG 소스 제한 필터
    #   parent_id : 재적재 시 기존 point 삭제 (embedding/pipeline.py)
    # create_payload_index는 이미 있어도 안전하게 재호출 가능하다.
    for field_name in ("keyword", "source", "parent_id"):
        client.create_payload_index(
            collection_name=name,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
```

`ensure_collection()`의 독스트링도 함께 갱신한다 (`keyword 필드의 payload 인덱스도` → `필터에 쓰이는 payload 인덱스도`).

- [ ] **Step 4: delete를 구현한다**

`embedding/pipeline.py`의 `_point_id` 함수 아래에 추가한다:

```python
def _build_delete_filter(parent_id: str) -> models.Filter:
    """이 문서에 속한 모든 청크 point를 고르는 필터."""
    return models.Filter(must=[
        models.FieldCondition(key="parent_id", match=models.MatchValue(value=parent_id))
    ])


def _delete_existing_points(parent_id: str) -> None:
    """재적재 전에 이 문서의 기존 point를 전부 지운다.

    point id가 uuid5(parent_id::chunk_index)로 결정론적이라 같은 인덱스는 upsert가
    덮어쓰지만, 청크 수가 줄면 뒤쪽 인덱스의 옛 point가 그대로 남는다. 그 point는
    keyword payload를 멀쩡히 갖고 있어 RAG 검색에 계속 잡힌다 — "저품질 청크 제거"
    작업이 오히려 저품질 청크를 고착시키게 된다.

    '재처리할 때만'이 아니라 '항상' 지운다. 조건부로 만들면 언젠가 조건이 틀린다.
    항상 지우고 넣으면 몇 번을 돌려도 상태가 같아진다(멱등). 비용은 문서당 delete 1회다.
    """
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.FilterSelector(filter=_build_delete_filter(parent_id)),
    )
```

`embed_documents()`의 try 블록(110~112행)을 다음으로 바꾼다:

```python
        try:
            points = _build_points(chunks)
            _delete_existing_points(str(doc["_id"]))
            client.upsert(collection_name=QDRANT_COLLECTION, points=points)
```

**순서가 중요하다.** `_build_points()`가 먼저다 — 임베딩 중 예외가 나면 delete가 실행되지 않아 기존 데이터가 살아남는다. delete를 먼저 하면 임베딩 실패 시 데이터만 지워지고 끝난다.

- [ ] **Step 5: 통과를 확인한다**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_reembed.py`
Expected: PASS — `[OK]` 3줄 후 `ALL PASS ✅`

- [ ] **Step 6: 기존 임베딩 모듈이 깨지지 않았는지 확인한다**

Run: `uv run python -c "import sys; sys.path.insert(0,'.'); from embedding.pipeline import embed_documents, _delete_existing_points; print('import OK')"`
Expected: `import OK`

(BGE-M3 모델을 로드하지 않는 import만 확인한다. 실제 임베딩 실행은 GPU와 Mongo가 필요하므로 여기서는 하지 않는다.)

- [ ] **Step 7: 전체 테스트를 돌린다**

Run:
```bash
PYTHONIOENCODING=utf-8 uv run python tests/test_matching.py
PYTHONIOENCODING=utf-8 uv run python tests/test_signals.py
PYTHONIOENCODING=utf-8 uv run python tests/test_funnel.py
PYTHONIOENCODING=utf-8 uv run python tests/test_reembed.py
PYTHONIOENCODING=utf-8 uv run python tests/test_crawl_parallel.py
```
Expected: 5개 파일 전부 `ALL PASS ✅`

- [ ] **Step 8: 커밋 (사용자 확인 후)**

```bash
git add DB/drant_clitent.py embedding/pipeline.py tests/test_reembed.py
git commit -m "fix(embedding): 재적재 시 고아 청크가 남던 문제 — parent_id 필터 delete 추가"
```

---

## 완료 확인

전부 끝나면 스펙 9절의 완료 기준을 하나씩 확인한다.

- [ ] **9.1** `uv run python tests/test_matching.py` → 개행 케이스 통과
- [ ] **9.2** `uv run python tests/test_reembed.py` → 고아 청크 0
- [ ] **9.3** `uv run python quality_test_main.py report before` → 기준선 숫자 출력
- [ ] **9.4** 컬렉션 이름이 `memes cleaned_memes mimori_chunks` 그대로
- [ ] **9.5** 테스트 5개 파일 전부 `ALL PASS ✅`
- [ ] `git status`에 `data_test/`가 안 보인다 (gitignore 확인)

기준선 리포트 출력은 **`docs/`에 복사해 기록으로 남기는 것을 권한다** — 2·3단계의 비교 대상이 된다.

### 스펙 §6 레벨 3(사람 라벨)에 대응하는 Task가 없는 이유

스펙의 품질 판단 3계층 중 레벨 1(자동 회귀 감지)과 레벨 2(확실히 나쁨 규칙)는 Task 7의
리포트·인스펙터가 담당한다. **레벨 3(사람 라벨 50~100건)은 1단계에 Task가 없다.**

스펙이 "규칙을 크게 바꿀 때만 재측정한다"고 정의했고, 1단계는 판정 로직을 바꾸지 않기
때문이다(Global Constraints). 라벨링이 필요해지는 시점은 judge를 넣는 2단계이며,
그때는 Task 7의 리포트로 어떤 청크를 표본으로 뽑을지 정할 수 있게 된다 — 순서가 맞다.

## 이후 단계

```
[2단계] 품질 게이트 — relevance judge, 크롤러 _is_relevant를 matching으로 수렴,
        짧은 키워드("67") 처리, 근접 중복 판정
[3단계] 청킹 개선 — 본문/댓글 항상 분리 + content_type, 최소 길이 10 → 30,
        chunk_document에 드롭 카운터 추가
```

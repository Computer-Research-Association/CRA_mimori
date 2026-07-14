# LLM 기반 밈 분석 기능 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이미 임베딩(Qdrant)까지 끝난 밈 키워드 하나를 선택하면, 해당 키워드의 전체 청크를 로컬 LLM(Ollama qwen3)에게 보여주고 사용 맥락/뉘앙스/뜻/의미 변화를 분석해주는 CLI 도구(`analyze_main.py`)를 만든다.

**Architecture:** 기존 `main.py` / `preprocess_main.py` / `embed_main.py`와 동일한 "루트 진입점 스크립트 + 전용 패키지" 패턴. `analysis/pipeline.py`가 MongoDB(키워드 목록)와 Qdrant(청크 조회)를 조회하고, 프롬프트를 조립해 Ollama에 넘긴다. 프롬프트 본문은 `analysis/prompt_template.md`에 분리해 코드 수정 없이 바꿀 수 있게 한다.

**Tech Stack:** Python 3.11, pymongo (`DB/mongo_client.py` 재사용), qdrant-client (`DB/drant_clitent.py` 재사용), ollama 파이썬 패키지 (`ollama.chat`).

## Global Constraints

- Python 3.11 고정 (`pyproject.toml`: `requires-python = ">=3.11,<3.12"`)
- 모든 스크립트는 저장소 루트에서 실행하는 것을 전제로 함 (`python analyze_main.py`), 기존 `*_main.py`처럼 `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`로 시작
- Windows 콘솔 한글 출력 깨짐 방지를 위해 진입점 스크립트에서 `sys.stdin/stdout/stderr.reconfigure(encoding="utf-8")` (win32에서만) — `embed_main.py`와 동일 패턴
- 이 프로젝트는 자동화된 테스트 프레임워크를 쓰지 않음(스펙에서 확정) — 각 태스크는 실제 스크립트/스니펫을 직접 실행해 눈으로 확인하는 방식으로 검증
- 새 설정값은 `config/config_cilent.py`에 추가 (`ANALYSIS_MODEL = "qwen3:latest"`, `ANALYSIS_PROMPT_PATH = "analysis/prompt_template.md"`)
- Qdrant 컬렉션명은 기존 `QDRANT_COLLECTION = "mimori_chunks"` 재사용 (신규 정의 안 함)
- 청크 결합 구분자는 `"\n\n---\n\n"` 고정 (스펙 명시)

---

### Task 1: 설정값 추가 + 프롬프트 템플릿 + analysis 패키지 스캐폴딩

**Files:**
- Modify: `config/config_cilent.py` (파일 끝에 추가)
- Create: `analysis/__init__.py`
- Create: `analysis/prompt_template.md`

**Interfaces:**
- Produces: `config.config_cilent.ANALYSIS_MODEL: str`, `config.config_cilent.ANALYSIS_PROMPT_PATH: str` — 이후 모든 태스크가 이 두 상수를 씀
- Produces: `analysis/prompt_template.md` — `{keyword}`, `{content}` 플레이스홀더를 포함한 텍스트 파일, Task 4가 이 파일을 읽음

- [ ] **Step 1: `config/config_cilent.py` 끝에 상수 추가**

파일 맨 아래에 다음을 추가한다:

```python

# LLM 분석 설정
ANALYSIS_MODEL = "qwen3:latest"
ANALYSIS_PROMPT_PATH = "analysis/prompt_template.md"
```

- [ ] **Step 2: `analysis/__init__.py` 생성 (빈 파일)**

`analysis/__init__.py` 파일을 내용 없이 생성한다 (기존 `preprocessing/__init__.py`, `embedding/__init__.py`와 동일).

- [ ] **Step 3: `analysis/prompt_template.md` 생성**

```markdown
다음은 밈/신조어 "{keyword}"에 대해 여러 출처에서 수집한 자료입니다.

{content}

위 자료를 바탕으로 다음을 분석해서 답변하세요:
1. 이 밈은 언제, 어떤 상황에서 쓰이는가
2. 어떤 뉘앙스(긍정/부정/유머 등)를 가지는가
3. 원래 뜻은 무엇이었는가
4. 그 뜻이 시간에 따라 어떻게 변화했는가 (변화가 없다면 없다고 명시)
```

- [ ] **Step 4: 설정값이 제대로 로드되는지 확인**

Run: `python -c "from config.config_cilent import ANALYSIS_MODEL, ANALYSIS_PROMPT_PATH; print(ANALYSIS_MODEL); print(ANALYSIS_PROMPT_PATH)"`

Expected:
```
qwen3:latest
analysis/prompt_template.md
```

- [ ] **Step 5: 프롬프트 템플릿 파일이 읽히는지 확인**

Run: `python -c "print(open('analysis/prompt_template.md', encoding='utf-8').read())"`

Expected: 위 Step 3에서 작성한 템플릿 전체가 그대로 출력됨 (`{keyword}`, `{content}` 플레이스홀더 포함).

- [ ] **Step 6: 커밋**

```bash
git add config/config_cilent.py analysis/__init__.py analysis/prompt_template.md
git commit -m "feat: add config and prompt template scaffolding for LLM meme analysis"
```

---

### Task 2: `list_analyzable_keywords()` 구현

**Files:**
- Create: `analysis/pipeline.py`

**Interfaces:**
- Consumes: `DB.mongo_client.get_collection() -> pymongo.collection.Collection` (기존 함수, 인자 없이 호출하면 기본 `memes` 컬렉션 반환)
- Produces: `analysis.pipeline.list_analyzable_keywords() -> list[str]` — Task 6이 이 함수를 씀

- [ ] **Step 1: `analysis/pipeline.py` 생성, `list_analyzable_keywords` 구현**

```python
"""
analysis/pipeline.py
이미 임베딩된 밈 키워드 목록 조회, Qdrant 청크 조회, 프롬프트 조립, LLM 분석 호출.
"""

from DB.mongo_client import get_collection


def list_analyzable_keywords() -> list[str]:
    """memes 컬렉션에서 is_embedded=True인 문서들의 distinct keyword 목록 반환 (정렬됨)."""
    collection = get_collection()
    keywords = collection.distinct("keyword", {"is_embedded": True})
    return sorted(keywords)
```

- [ ] **Step 2: 실제 MongoDB에 대고 동작 확인**

`feature/embedding` 머지(`4c44403`) 이후 `embed_main.py`를 이미 돌려서 `is_embedded=True`인 문서가 최소 1건 이상 있어야 한다. 없다면 먼저 `python embed_main.py`를 Enter만 눌러 전체 처리로 한 번 실행한다.

Run: `python -c "from analysis.pipeline import list_analyzable_keywords; print(list_analyzable_keywords())"`

Expected: 문자열 리스트가 출력됨, 예: `['갈아넣다', '손절', '억까']` (실제 값은 크롤링된 데이터에 따라 다름). 빈 리스트 `[]`가 나오면 위 사전 조건(임베딩 완료 문서 존재)을 다시 확인한다.

- [ ] **Step 3: 커밋**

```bash
git add analysis/pipeline.py
git commit -m "feat: add list_analyzable_keywords for LLM meme analysis"
```

---

### Task 3: `fetch_keyword_chunks(keyword)` 구현

**Files:**
- Modify: `analysis/pipeline.py`

**Interfaces:**
- Consumes: `DB.drant_clitent.client: qdrant_client.QdrantClient` (기존 인스턴스), `config.config_cilent.QDRANT_COLLECTION: str` (기존 상수, 값 `"mimori_chunks"`)
- Produces: `analysis.pipeline.fetch_keyword_chunks(keyword: str) -> list[str]` — Task 6이 이 함수를 씀

- [ ] **Step 1: `analysis/pipeline.py`에 import 및 함수 추가**

파일 상단 import에 추가:

```python
from qdrant_client.http import models

from config.config_cilent import QDRANT_COLLECTION
from DB.drant_clitent import client
```

`list_analyzable_keywords` 아래에 추가:

```python
_SCROLL_BATCH_SIZE = 100


def fetch_keyword_chunks(keyword: str) -> list[str]:
    """Qdrant mimori_chunks에서 payload.keyword == keyword인 포인트를 전부 가져와
    payload["text"] 리스트로 반환. 없으면 빈 리스트."""
    scroll_filter = models.Filter(
        must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    )

    texts = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=scroll_filter,
            with_payload=True,
            limit=_SCROLL_BATCH_SIZE,
            offset=offset,
        )
        texts.extend(point.payload["text"] for point in points)
        if offset is None:
            break
    return texts
```

- [ ] **Step 2: 실제 Qdrant에 대고 동작 확인**

Task 2의 Step 2에서 얻은 키워드 목록 중 하나(`<keyword>`)를 그대로 써서:

Run: `python -c "from analysis.pipeline import fetch_keyword_chunks; chunks = fetch_keyword_chunks('<keyword>'); print(len(chunks)); print(chunks[0][:100])"`

Expected: `len(chunks)`가 0보다 큰 정수로 출력되고, 이어서 첫 청크 텍스트의 앞 100자가 출력됨. 존재하지 않는 임의의 키워드(예: `'존재하지않는키워드'`)로 다시 실행하면 `0`과 `IndexError`(빈 리스트라 `chunks[0]` 접근 실패)가 나는 것도 확인 — 이건 Task 6에서 빈 리스트 처리로 감쌀 부분이라 정상.

- [ ] **Step 3: 커밋**

```bash
git add analysis/pipeline.py
git commit -m "feat: add fetch_keyword_chunks for LLM meme analysis"
```

---

### Task 4: `build_prompt(keyword, chunks)` 구현

**Files:**
- Modify: `analysis/pipeline.py`

**Interfaces:**
- Consumes: `config.config_cilent.ANALYSIS_PROMPT_PATH: str` (Task 1에서 추가), `analysis/prompt_template.md` 파일 (Task 1에서 생성)
- Produces: `analysis.pipeline.build_prompt(keyword: str, chunks: list[str]) -> str` — Task 6이 이 함수를 씀

- [ ] **Step 1: `analysis/pipeline.py`에 import 및 함수 추가**

파일 상단 import에 추가:

```python
from config.config_cilent import ANALYSIS_PROMPT_PATH
```

`fetch_keyword_chunks` 아래에 추가:

```python
_CHUNK_SEPARATOR = "\n\n---\n\n"


def build_prompt(keyword: str, chunks: list[str]) -> str:
    """prompt_template.md를 읽어 {keyword}, {content}를 채운 문자열 반환."""
    with open(ANALYSIS_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    content = _CHUNK_SEPARATOR.join(chunks)
    return template.format(keyword=keyword, content=content)
```

- [ ] **Step 2: 순수 함수 동작 확인 (외부 서비스 불필요)**

Run: `python -c "from analysis.pipeline import build_prompt; print(build_prompt('테스트키워드', ['첫번째 청크 내용', '두번째 청크 내용']))"`

Expected: 아래와 같이 `{keyword}` 자리에 `테스트키워드`, `{content}` 자리에 두 청크가 `---`로 구분되어 채워진 텍스트가 출력됨:
```
다음은 밈/신조어 "테스트키워드"에 대해 여러 출처에서 수집한 자료입니다.

첫번째 청크 내용

---

두번째 청크 내용

위 자료를 바탕으로 다음을 분석해서 답변하세요:
1. 이 밈은 언제, 어떤 상황에서 쓰이는가
2. 어떤 뉘앙스(긍정/부정/유머 등)를 가지는가
3. 원래 뜻은 무엇이었는가
4. 그 뜻이 시간에 따라 어떻게 변화했는가 (변화가 없다면 없다고 명시)
```

- [ ] **Step 3: 커밋**

```bash
git add analysis/pipeline.py
git commit -m "feat: add build_prompt for LLM meme analysis"
```

---

### Task 5: `analyze(prompt, model)` 구현

**Files:**
- Modify: `analysis/pipeline.py`

**Interfaces:**
- Consumes: `config.config_cilent.ANALYSIS_MODEL: str` (Task 1에서 추가, 기본값 `"qwen3:latest"`), 로컬에서 실행 중인 Ollama 서버 (모델 `qwen3:latest` pull 완료 전제)
- Produces: `analysis.pipeline.analyze(prompt: str, model: str = ANALYSIS_MODEL) -> str` — Task 6이 이 함수를 씀

- [ ] **Step 1: `analysis/pipeline.py`에 import 및 함수 추가**

파일 상단 import에 추가:

```python
import ollama

from config.config_cilent import ANALYSIS_MODEL
```

`build_prompt` 아래에 추가:

```python
def analyze(prompt: str, model: str = ANALYSIS_MODEL) -> str:
    """prompt를 model에 보내 분석 결과 텍스트를 반환."""
    response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
    return response.message.content
```

- [ ] **Step 2: 실제 Ollama에 대고 동작 확인**

Ollama가 로컬에서 실행 중이어야 한다 (`qwen3:latest`는 이미 pull되어 있음, 확인: `ollama list`).

Run: `python -c "from analysis.pipeline import analyze; print(analyze('안녕, 너는 무슨 모델이야? 한 문장으로 답해줘.'))"`

Expected: 빈 문자열이 아닌, qwen3가 생성한 한국어/영어 응답 텍스트가 출력됨. Ollama 서버가 꺼져 있으면 연결 에러(`ConnectionError` 계열)가 그대로 나는 것도 확인 — Task 6에서 그 예외 메시지를 그대로 출력하고 종료하는 방식으로 처리할 부분이라 정상.

- [ ] **Step 3: 커밋**

```bash
git add analysis/pipeline.py
git commit -m "feat: add analyze for LLM meme analysis"
```

---

### Task 6: `analyze_main.py` 진입점 작성 (전체 플로우 연결)

**Files:**
- Create: `analyze_main.py`

**Interfaces:**
- Consumes: `analysis.pipeline.list_analyzable_keywords() -> list[str]`, `analysis.pipeline.fetch_keyword_chunks(keyword: str) -> list[str]`, `analysis.pipeline.build_prompt(keyword: str, chunks: list[str]) -> str`, `analysis.pipeline.analyze(prompt: str) -> str` (Task 2~5에서 만든 4개 함수 전부)

- [ ] **Step 1: `analyze_main.py` 작성**

```python
"""
analyze_main.py
이미 임베딩된 밈 키워드 중 하나를 선택하면, Qdrant에 저장된 해당 키워드의
전체 청크를 로컬 LLM(Ollama)에게 보여주고 분석 결과를 받아 출력한다.

main.py / preprocess_main.py / embed_main.py와 동일하게 루트에서 바로 실행 가능:
    python analyze_main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from analysis.pipeline import (
    analyze,
    build_prompt,
    fetch_keyword_chunks,
    list_analyzable_keywords,
)

if __name__ == "__main__":
    keywords = list_analyzable_keywords()
    if not keywords:
        print("분석할 수 있는 키워드가 없습니다.")
        sys.exit(1)

    print("분석 가능한 키워드:")
    for i, keyword in enumerate(keywords, start=1):
        print(f"  {i}. {keyword}")

    raw_choice = input("키워드 선택 (번호 입력): ").strip()
    if not raw_choice.isdigit() or not (1 <= int(raw_choice) <= len(keywords)):
        print("잘못된 선택입니다.")
        sys.exit(1)

    selected_keyword = keywords[int(raw_choice) - 1]

    print(f"[조회 중] '{selected_keyword}' 관련 청크 수집...")
    chunks = fetch_keyword_chunks(selected_keyword)
    if not chunks:
        print("Qdrant에서 청크를 찾을 수 없습니다.")
        sys.exit(1)
    print(f"[조회 완료] 청크 {len(chunks)}개 수집됨")

    prompt = build_prompt(selected_keyword, chunks)

    print("[분석 중] LLM에게 질의 중...")
    result = analyze(prompt)

    print()
    print("=" * 40)
    print(f"'{selected_keyword}' 분석 결과")
    print("=" * 40)
    print(result)
```

- [ ] **Step 2: 전체 플로우 end-to-end 실행 확인**

Run: `python analyze_main.py`

- 키워드 번호 목록이 출력되는지 확인
- 목록에 있는 번호(예: `1`)를 입력해 정상적으로 "조회 중 → 조회 완료 → 분석 중 → 분석 결과" 순서로 진행되고, 마지막에 LLM이 생성한 분석 텍스트가 출력되는지 확인
- 다시 실행해서 목록에 없는 번호(예: `999`)를 입력하면 "잘못된 선택입니다"가 출력되고 종료되는지 확인
- 다시 실행해서 숫자가 아닌 값(예: `abc`)을 입력해도 "잘못된 선택입니다"가 출력되고 종료되는지 확인 (`isdigit()` 체크 덕분)

- [ ] **Step 3: 커밋**

```bash
git add analyze_main.py
git commit -m "feat: add analyze_main.py CLI entry point for LLM meme analysis"
```

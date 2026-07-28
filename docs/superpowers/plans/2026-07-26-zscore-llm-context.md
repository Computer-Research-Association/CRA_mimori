# z-score(유행 상태) LLM 프롬프트 반영 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 밈 키워드의 네이버 데이터랩 기반 유행 상태(z-score)를 `analyze_main.py`/`rag_main.py`의 실제 LLM 프롬프트와 `analysis/langchain_playground.ipynb`에 반영하고, 노트북에서는 포함 여부를 토글로 선택할 수 있게 한다.

**Architecture:** 이미 존재하는 `trend/trend_service.get_meme_trend()`를 감싸는 새 함수 `format_trend_context(keyword)`를 `trend/trend_service.py`에 하나 추가하고, `analysis/pipeline.py`(`build_prompt`)와 `analysis/rag_pipeline.py`(`build_rag_prompt`)가 이 함수를 호출해 프롬프트 템플릿의 새 `{trend_info}` 자리표시자를 채운다. 노트북은 같은 함수를 셀에서 직접 호출하고 `INCLUDE_TREND` 불리언 변수로 프롬프트에 넣을지 결정한다.

**Tech Stack:** Python, 네이버 데이터랩 API(`requests`), 기존 LangChain/NVIDIA/Qdrant/MongoDB 스택.

## Global Constraints

- `trend/zscore.py`, `trend/datalab_client.py`, `trend/trend_service.py`의 기존 함수(`get_meme_trend`, `get_zscore`, `robust_scale`, `classify_trend`, `DataLabClient`)는 로직을 변경하지 않는다 — 그대로 재사용.
- `format_trend_context()`는 어떤 이유로든(API 키 미설정, 네트워크 오류, 검색량 데이터 없음) z-score를 가져오지 못하면 빈 문자열 `""`을 반환한다 — 예외를 호출부로 전파하지 않는다.
- CLI(`analyze_main.py`, `rag_main.py`)는 포함 여부를 묻지 않고 항상 자동으로 시도한다.
- 노트북(`analysis/langchain_playground.ipynb`)만 `INCLUDE_TREND` 변수로 포함 여부를 토글한다.
- 이 프로젝트에는 자동화된 pytest 테스트가 없다 — 각 태스크의 검증은 실제 스크립트를 실행해 눈으로 결과를 확인하는 방식이다. `.env`에 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`가 이미 설정되어 있으므로 실제 데이터랩 API를 호출해 검증할 수 있다.
- 참고 스펙: `docs/superpowers/specs/2026-07-26-zscore-llm-context-design.md`

---

### Task 1: `trend/trend_service.py`에 `format_trend_context()` 추가

**Files:**
- Modify: `trend/trend_service.py`

**Interfaces:**
- Consumes: 기존 `get_meme_trend(keyword: str, related_keywords: list[str] = None) -> dict`(같은 파일에 이미 정의됨, 반환값 `{"keyword": str, "z_score": float, "status": str, "ratios": list[dict]}`).
- Produces: `format_trend_context(keyword: str) -> str` — Task 2, 3, 4에서 이 함수를 그대로 import해서 사용한다.

- [ ] **Step 1: 함수 추가**

`trend/trend_service.py` 파일 끝(`if __name__ == "__main__":` 블록 앞)에 아래 함수를 추가한다:

```python
def format_trend_context(keyword: str) -> str:
    """
    get_meme_trend()를 호출해 LLM 프롬프트에 넣을 유행 상태 텍스트를 만든다.

    데이터랩 API 키 미설정, 네트워크 오류, 검색량 데이터 없음 등 어떤 이유로든
    조회할 수 없으면 빈 문자열을 반환한다 — 호출부는 이 빈 문자열을 그대로
    프롬프트 조립에 써도 안전하며, RAG 핵심 기능은 막히지 않는다.

    ratios가 비어있는 경우(데이터랩에 해당 키워드 검색량 데이터가 전혀 없음)도
    빈 문자열을 반환한다 — get_zscore([])는 (0.0, "유행 중")을 반환하는데, 이걸
    그대로 노출하면 "데이터 없음"이 "유행 중"으로 잘못 전달되기 때문이다.
    """
    try:
        result = get_meme_trend(keyword)
    except Exception:
        return ""
    if not result["ratios"]:
        return ""
    return (
        f"[참고: 최근 검색량 기준 유행 상태 — 정성적 분석의 보조 지표로만 활용]\n"
        f"상태: {result['status']} (robust z-score: {result['z_score']:.2f})\n"
    )
```

전체 파일은 이렇게 된다 (기존 코드 + 새 함수, `if __name__` 블록은 그대로 유지):

```python
"""
trend_service.py
밈 이름 하나를 받아 유행 상태를 판정하는 상위 서비스 함수.

DataLabClient(검색량 수집) + zscore(Robust Scaling 판정)를 조합한다.
크롤링 데이터(MongoDB)는 여기서 쓰지 않는다 - cold-start / 노이즈 문제로
트렌드 판정에서는 배제하고 RAG 근거 자료로만 활용하기로 결정.
"""

from trend.datalab_client import DataLabClient
from trend.zscore import get_zscore


def get_meme_trend(keyword: str, related_keywords: list[str] = None) -> dict:
    """
    밈 이름으로 유행 상태를 판정한다.

    related_keywords 기본값: ["{keyword} 뜻", "{keyword}가 뭐야"]
    (검색 변형을 합산해 실제 관심도를 더 잘 반영하기 위함)

    반환:
        {
            "keyword": str,
            "z_score": float,
            "status": str,          # 핫함 / 유행 중 / 감소 / 소멸
            "ratios": list[dict],   # [{"date", "ratio"}, ...]
        }
    """
    if related_keywords is None:
        related_keywords = [f"{keyword} 뜻", f"{keyword}가 뭐야"]

    client = DataLabClient()
    ratios = client.get_recent_ratios(keyword, related_keywords=related_keywords)

    z, status = get_zscore(ratios)

    return {
        "keyword": keyword,
        "z_score": z,
        "status": status,
        "ratios": ratios,
    }


def format_trend_context(keyword: str) -> str:
    """
    get_meme_trend()를 호출해 LLM 프롬프트에 넣을 유행 상태 텍스트를 만든다.

    데이터랩 API 키 미설정, 네트워크 오류, 검색량 데이터 없음 등 어떤 이유로든
    조회할 수 없으면 빈 문자열을 반환한다 — 호출부는 이 빈 문자열을 그대로
    프롬프트 조립에 써도 안전하며, RAG 핵심 기능은 막히지 않는다.

    ratios가 비어있는 경우(데이터랩에 해당 키워드 검색량 데이터가 전혀 없음)도
    빈 문자열을 반환한다 — get_zscore([])는 (0.0, "유행 중")을 반환하는데, 이걸
    그대로 노출하면 "데이터 없음"이 "유행 중"으로 잘못 전달되기 때문이다.
    """
    try:
        result = get_meme_trend(keyword)
    except Exception:
        return ""
    if not result["ratios"]:
        return ""
    return (
        f"[참고: 최근 검색량 기준 유행 상태 — 정성적 분석의 보조 지표로만 활용]\n"
        f"상태: {result['status']} (robust z-score: {result['z_score']:.2f})\n"
    )


if __name__ == "__main__":
    _keyword = input("키워드 입력: ").strip()
    result = get_meme_trend(_keyword)
    print(f"키워드: {result['keyword']}")
    print(f"z_score: {result['z_score']:.4f}")
    print(f"상태:    {result['status']}")
    print(f"데이터 {len(result['ratios'])}건")
```

- [ ] **Step 2: 문법 검증**

Run: `python -c "import ast; ast.parse(open('trend/trend_service.py', encoding='utf-8').read())"`
Expected: 에러 없이 종료 (출력 없음).

- [ ] **Step 3: 실제 데이터랩 API로 동작 확인**

`.env`에 `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`가 이미 설정되어 있다. 실제로 검색량이 있을 법한 키워드(예: "김치") 하나와, 존재하지 않을 법한 임의의 키워드로 각각 확인한다:

```bash
python -c "
from trend.trend_service import format_trend_context

result1 = format_trend_context('김치')
print('--- 실제 키워드 결과 ---')
print(repr(result1))
assert isinstance(result1, str)

result2 = format_trend_context('asdkjhqwlekjhasdkjhzzzzz999')
print('--- 존재하지 않을 법한 키워드 결과 ---')
print(repr(result2))
assert isinstance(result2, str)

print('OK')
"
```
Expected: 두 호출 모두 예외 없이 문자열을 반환. 실제 검색량이 있는 키워드는 `[참고: ...]`로 시작하는 비어있지 않은 문자열이 나오고, 존재하지 않을 법한 키워드는 `''`(빈 문자열)이 나올 가능성이 높다(데이터랩이 실제로 어떻게 응답하는지에 따라 다를 수 있음 — 핵심은 예외가 나지 않고 항상 문자열을 반환한다는 것). 마지막 줄 `OK` 출력.

- [ ] **Step 4: Commit**

```bash
git add trend/trend_service.py
git commit -m "feat: trend_service에 format_trend_context 추가"
```

---

### Task 2: `analysis/pipeline.py` + `analysis/prompt_template.md`에 유행 상태 반영

**Files:**
- Modify: `analysis/pipeline.py`
- Modify: `analysis/prompt_template.md`

**Interfaces:**
- Consumes: `format_trend_context(keyword: str) -> str` (Task 1, `trend.trend_service`).
- Produces: 없음 (CLI 진입점 `analyze_main.py`가 이 변경을 통해 자동으로 혜택을 받음, 코드 변경 없음).

- [ ] **Step 1: import 추가**

`analysis/pipeline.py`에서 아래 줄을 찾는다:

```python
from config.config_cilent import ANALYSIS_MODEL, ANALYSIS_PROMPT_PATH, NIM_KEY, QDRANT_COLLECTION
from DB.drant_clitent import client, ensure_collection
from DB.mongo_client import get_collection
```

이것을 아래로 교체한다:

```python
from config.config_cilent import ANALYSIS_MODEL, ANALYSIS_PROMPT_PATH, NIM_KEY, QDRANT_COLLECTION
from DB.drant_clitent import client, ensure_collection
from DB.mongo_client import get_collection
from trend.trend_service import format_trend_context
```

- [ ] **Step 2: `build_prompt` 수정**

아래 함수를 찾는다:

```python
def build_prompt(keyword: str, chunks: list[str]) -> str:
    """prompt_template.md를 읽어 {keyword}, {content}를 채운 문자열 반환."""
    with open(ANALYSIS_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    content = _CHUNK_SEPARATOR.join(chunks)
    return template.format(keyword=keyword, content=content)
```

이것을 아래로 교체한다:

```python
def build_prompt(keyword: str, chunks: list[str]) -> str:
    """prompt_template.md를 읽어 {keyword}, {content}, {trend_info}를 채운 문자열 반환."""
    with open(ANALYSIS_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    content = _CHUNK_SEPARATOR.join(chunks)
    trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, content=content, trend_info=trend_info)
```

- [ ] **Step 3: `analysis/prompt_template.md` 전체 교체**

파일 전체를 아래 내용으로 교체한다:

```
다음은 밈/신조어 "{keyword}"에 대해 여러 출처에서 수집한 자료입니다.

{trend_info}
{content}

위 자료를 바탕으로 다음을 분석해서 답변하세요:
1. 이 밈은 언제, 어떤 상황에서 쓰이는가
2. 어떤 뉘앙스(긍정/부정/유머 등)를 가지는가
3. 원래 뜻은 무엇이었는가
4. 그 뜻이 시간에 따라 어떻게 변화했는가 (변화가 없다면 없다고 명시)
```

- [ ] **Step 4: 문법 검증**

Run: `python -c "import ast; ast.parse(open('analysis/pipeline.py', encoding='utf-8').read())"`
Expected: 에러 없이 종료.

- [ ] **Step 5: 실제 동작 확인**

전제조건: MongoDB에 `is_embedded=True`인 키워드가 최소 1개 있어야 한다(`list_analyzable_keywords()`가 빈 리스트를 반환하면 이 스크립트는 그 키워드로 테스트할 수 없다 — 이 경우 아래 명령 대신 `build_prompt`만 직접 테스트한다).

```bash
python -c "
from analysis.pipeline import list_analyzable_keywords, build_prompt

keywords = list_analyzable_keywords()
if keywords:
    keyword = keywords[0]
else:
    keyword = '김치'  # 임베딩된 키워드가 없으면 임의 키워드로 build_prompt 포맷팅만 확인

prompt = build_prompt(keyword, ['테스트 청크 내용입니다.'])
print(prompt)
assert '{trend_info}' not in prompt  # 자리표시자가 실제 값으로 치환됐는지 확인
assert '{keyword}' not in prompt
assert '{content}' not in prompt
print()
print('OK')
"
```
Expected: 프롬프트 전문이 출력되고, 데이터랩에서 실제로 유행 상태를 가져왔다면 `[참고: 최근 검색량 기준 유행 상태...]`로 시작하는 블록이 보인다(못 가져왔다면 그 블록 없이 자연스럽게 이어짐). 마지막 줄 `OK`.

- [ ] **Step 6: Commit**

```bash
git add analysis/pipeline.py analysis/prompt_template.md
git commit -m "feat: analyze_main 프롬프트에 유행 상태(z-score) 반영"
```

---

### Task 3: `analysis/rag_pipeline.py` + `analysis/rag_prompt_template.md`에 유행 상태 반영

**Files:**
- Modify: `analysis/rag_pipeline.py`
- Modify: `analysis/rag_prompt_template.md`

**Interfaces:**
- Consumes: `format_trend_context(keyword: str) -> str` (Task 1, `trend.trend_service`).
- Produces: 없음 (CLI 진입점 `rag_main.py`가 이 변경을 통해 자동으로 혜택을 받음, 코드 변경 없음).

- [ ] **Step 1: import 추가**

`analysis/rag_pipeline.py` 맨 위 import 블록을 찾는다:

```python
from config.config_cilent import (
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
    RAG_PROMPT_PATH,
    RAG_TOP_K,
)
from DB.drant_clitent import client
from embedding.pipeline import _to_sparse_vector
```

이것을 아래로 교체한다:

```python
from config.config_cilent import (
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
    RAG_PROMPT_PATH,
    RAG_TOP_K,
)
from DB.drant_clitent import client
from embedding.pipeline import _to_sparse_vector
from trend.trend_service import format_trend_context
```

- [ ] **Step 2: `build_rag_prompt` 수정**

아래 함수를 찾는다:

```python
def build_rag_prompt(keyword: str, question: str, points: list[models.ScoredPoint]) -> str:
    """
    rag_prompt_template.md를 읽어 {keyword}/{context}/{question}을 채운 문자열 반환.
    {context}는 각 포인트를 '[출처: {title} / {url}]\n{text}' 형태로 만들어 이어붙인 것.
    """
    with open(RAG_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    context_parts = []
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = point.payload.get("url") or "출처 없음"
        text = point.payload.get("text", "")
        context_parts.append(f"[출처: {title} / {url}]\n{text}")

    context = _CONTEXT_SEPARATOR.join(context_parts)
    return template.format(keyword=keyword, context=context, question=question)
```

이것을 아래로 교체한다:

```python
def build_rag_prompt(keyword: str, question: str, points: list[models.ScoredPoint]) -> str:
    """
    rag_prompt_template.md를 읽어 {keyword}/{context}/{question}/{trend_info}를 채운 문자열 반환.
    {context}는 각 포인트를 '[출처: {title} / {url}]\n{text}' 형태로 만들어 이어붙인 것.
    """
    with open(RAG_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    context_parts = []
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = point.payload.get("url") or "출처 없음"
        text = point.payload.get("text", "")
        context_parts.append(f"[출처: {title} / {url}]\n{text}")

    context = _CONTEXT_SEPARATOR.join(context_parts)
    trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, context=context, question=question, trend_info=trend_info)
```

- [ ] **Step 3: `analysis/rag_prompt_template.md` 전체 교체**

파일 전체를 아래 내용으로 교체한다:

```
다음은 밈/신조어 "{keyword}"에 대해 수집된 자료 중, 아래 질문과 관련성이 높은 것들입니다.

{trend_info}
{context}

위 자료를 근거로 다음 질문에 답변하세요. 자료에 없는 내용은 추측하지 말고 모른다고 답하세요.

질문: {question}
```

- [ ] **Step 4: 문법 검증**

Run: `python -c "import ast; ast.parse(open('analysis/rag_pipeline.py', encoding='utf-8').read())"`
Expected: 에러 없이 종료.

- [ ] **Step 5: 실제 동작 확인**

```bash
python -c "
from analysis.rag_pipeline import build_rag_prompt

class FakePoint:
    def __init__(self, payload):
        self.payload = payload

points = [FakePoint({'title': '테스트 제목', 'url': 'http://example.com', 'text': '테스트 본문'})]
prompt = build_rag_prompt('김치', '이건 무슨 뜻이야?', points)
print(prompt)
assert '{trend_info}' not in prompt
assert '{keyword}' not in prompt
assert '{context}' not in prompt
assert '{question}' not in prompt
print()
print('OK')
"
```
Expected: 프롬프트 전문이 출력되고, 데이터랩에서 실제로 유행 상태를 가져왔다면 `[참고: 최근 검색량 기준 유행 상태...]` 블록이 보인다(못 가져왔다면 자연스럽게 빠짐). 마지막 줄 `OK`.

- [ ] **Step 6: Commit**

```bash
git add analysis/rag_pipeline.py analysis/rag_prompt_template.md
git commit -m "feat: rag_main 프롬프트에 유행 상태(z-score) 반영"
```

---

### Task 4: `analysis/langchain_playground.ipynb`에 z-score 셀 + 토글 추가

**Files:**
- Modify: `analysis/langchain_playground.ipynb`

**Interfaces:**
- Consumes: `format_trend_context(keyword: str) -> str` (Task 1, `trend.trend_service`). 노트북 안의 기존 변수 `KEYWORD`(2번 셀에서 정의), `fused_points`(검색 결과 셀에서 정의).
- Produces: 노트북 전역 변수 `trend_info: str`, `INCLUDE_TREND: bool` — 프롬프트 작성 셀에서 사용.

이 태스크는 `NotebookEdit` 도구로 수행한다(일반 텍스트 편집 도구는 `.ipynb`에 쓸 수 없다). 먼저 `Read` 도구로 `analysis/langchain_playground.ipynb`를 읽어 각 셀의 `id`를 확인한다. 이 계획을 작성한 시점 기준 셀 구성은 다음과 같다(15개 셀, id는 `cell-0` ~ `cell-14`):

- `cell-0`: 인트로 markdown
- `cell-1`: "1. 환경 설정" markdown
- `cell-2`: 환경 설정 code
- `cell-3`: "2. 키워드 선택" markdown
- `cell-4`: 키워드 선택 code (`KEYWORD` 정의)
- `cell-5`: "3. 질문 입력 + 임베딩" markdown
- `cell-6`: 질문+임베딩 code
- `cell-7`: "4. 검색 파라미터 + 실행" markdown
- `cell-8`: 검색 실행 code
- `cell-9`: "5. 검색 결과 확인" markdown
- `cell-10`: 검색 결과 출력 code
- `cell-11`: "6. 프롬프트 작성" markdown
- `cell-12`: 프롬프트 작성 code
- `cell-13`: "7. LLM 호출" markdown
- `cell-14`: LLM 호출 code

만약 `Read` 결과의 셀 id가 이것과 다르면(예: 이전 태스크에서 셀이 추가/재정렬됐다면), 실제 `Read` 출력에 나온 id를 기준으로 아래 단계의 `cell_id`를 맞춰 사용한다 — id 문자열 자체가 아니라 "키워드 선택 code 셀 다음", "프롬프트 작성 md/code 셀" 같은 위치 설명이 진짜 기준이다.

- [ ] **Step 1: 새 markdown 셀 삽입 (z-score 섹션 제목)**

`NotebookEdit`을 `edit_mode="insert"`, `cell_id="cell-4"`(키워드 선택 code 셀), `cell_type="markdown"`으로 호출해 아래 내용을 삽입한다(cell-4 바로 뒤에 삽입됨):

```markdown
## 2.5. z-score / 유행 상태 확인

**이 셀이 하는 일**: 선택한 키워드(`KEYWORD`)의 최근 검색량 기반 유행 상태를 네이버 데이터랩에서 조회합니다.

**실험하려면**: 아래 코드 셀의 `INCLUDE_TREND`를 `True`/`False`로 바꾼 뒤, 이 셀을 먼저 재실행하고 "6. 프롬프트 작성" 셀을 실행해야 반영됩니다(`INCLUDE_TREND`는 이 셀에서 정의되므로, 값만 바꾸고 이 셀을 다시 실행하지 않으면 이전 값이 그대로 남아있습니다).
```

- [ ] **Step 2: 삽입된 markdown 셀의 id 확인**

`Read` 도구로 노트북을 다시 읽어, Step 1에서 삽입된 markdown 셀의 `id`를 확인한다(예: `cell-5`처럼 새로 부여된 id).

- [ ] **Step 3: 새 code 셀 삽입 (z-score 조회)**

`NotebookEdit`을 `edit_mode="insert"`, `cell_id=<Step 2에서 확인한 id>`, `cell_type="code"`로 호출해 아래 내용을 삽입한다:

```python
from trend.trend_service import format_trend_context

trend_info = format_trend_context(KEYWORD)
if trend_info:
    print(trend_info)
else:
    print("z-score/유행 상태를 가져오지 못했습니다 (NAVER API 키 미설정이거나 데이터 없음).")

INCLUDE_TREND = True
```

- [ ] **Step 4: "6. 프롬프트 작성" markdown 셀 갱신**

`Read`로 노트북을 다시 읽어 "## 6. 프롬프트 작성"으로 시작하는 markdown 셀의 id를 확인한다. `NotebookEdit`을 `edit_mode="replace"`, 그 `cell_id`, `cell_type="markdown"`으로 호출해 내용을 아래로 교체한다:

```markdown
## 6. 프롬프트 작성

**이 셀이 하는 일**: 검색된 청크(`fused_points`)와 유행 상태 정보(`trend_info`, `INCLUDE_TREND`가 `True`일 때만)를 근거 자료로 넣어 LLM에게 실제로 전달할 프롬프트를 완성합니다.

**실험하려면**: `PROMPT_TEMPLATE` 문자열을 자유롭게 수정하세요(지시문 추가, 어조 변경, 출력 형식 지정 등). `{keyword}` / `{trend_info}` / `{context}` / `{question}` 네 자리표시자는 반드시 그대로 남겨둬야 합니다. `.format()`을 쓰므로, 프롬프트에 `{`/`}` 자체를 문자로 넣고 싶으면(예: JSON 출력 형식을 지시하는 경우) `{{`/`}}`로 이스케이프해야 합니다.
```

- [ ] **Step 5: "6. 프롬프트 작성" code 셀 갱신**

같은 방식으로 프롬프트 작성 code 셀(`PROMPT_TEMPLATE = """...` 로 시작하는 셀)의 id를 확인하고, `NotebookEdit`을 `edit_mode="replace"`, `cell_type="code"`로 호출해 내용을 아래로 교체한다:

```python
PROMPT_TEMPLATE = """당신은 밈/신조어 분석 전문가입니다.

키워드: {keyword}

{trend_info}
자료:
{context}

질문: {question}

위 자료를 근거로 답변하세요. 자료에 없는 내용은 추측하지 말고 모른다고 답하세요.
"""

context = "\n\n---\n\n".join(
    f"[출처: {p.payload.get('title') or '제목 없음'} / {p.payload.get('url') or '출처 없음'}]\n{p.payload.get('text', '')}"
    for p in fused_points
)
prompt = PROMPT_TEMPLATE.format(
    keyword=KEYWORD,
    trend_info=(trend_info if INCLUDE_TREND else ""),
    context=context,
    question=QUESTION,
)
print(prompt)
```

- [ ] **Step 6: 셀 개수 및 JSON 유효성 검증**

Run: `python -c "import json; nb = json.load(open('analysis/langchain_playground.ipynb', encoding='utf-8')); print(len(nb['cells']), 'cells')"`
Expected: `17 cells` (기존 15개 + markdown 1개 + code 1개).

- [ ] **Step 7: 모든 코드 셀 구문 검증**

Run:
```bash
python -c "
import json, ast
nb = json.load(open('analysis/langchain_playground.ipynb', encoding='utf-8'))
for cell in nb['cells']:
    if cell['cell_type'] == 'code':
        ast.parse(''.join(cell['source']))
print('모든 코드 셀 구문 정상')
"
```
Expected: `모든 코드 셀 구문 정상` 출력, 예외 없음.

- [ ] **Step 8: (전제조건 충족 시) Jupyter에서 전체 실행으로 기능 검증**

전제조건: Qdrant/MongoDB가 `mimori-flask` 컨테이너 안에서 접근 가능한 상태(또는 `.env`를 localhost로 맞춘 상태)이고, 최소 1개 키워드가 임베딩되어 있어야 한다.

`jupyter lab`(또는 IDE의 Jupyter 확장)으로 노트북을 열고 "Run All"을 실행한다.

Expected: 새로 추가된 z-score 셀이 유행 상태 텍스트(또는 "가져오지 못했습니다" 메시지)를 출력하고, 프롬프트 작성 셀 출력에 `INCLUDE_TREND = True`일 때 `[참고: 최근 검색량 기준 유행 상태...]` 블록이 포함되는지 확인한다. 그 다음 `INCLUDE_TREND = False`로 바꾸고 프롬프트 작성 셀부터 재실행했을 때 그 블록이 사라지는 것을 확인한다.

- [ ] **Step 9: Commit**

```bash
git add analysis/langchain_playground.ipynb
git commit -m "feat: langchain_playground 노트북에 z-score 조회 및 INCLUDE_TREND 토글 추가"
```

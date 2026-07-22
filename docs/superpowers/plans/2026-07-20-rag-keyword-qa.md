# RAG 기반 키워드 질의응답 기능 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 자유 텍스트 질문을 입력하면, 그 질문을 임베딩해 Qdrant에서 하이브리드(dense+sparse) 벡터 유사도 검색으로 관련 청크만 뽑고, 그걸 근거로 로컬 LLM(Ollama)이 답변하는 `rag_main.py` CLI 도구를 추가한다.

**Architecture:** 기존 `main.py`/`preprocess_main.py`/`embed_main.py`/`analyze_main.py`와 동일한 "루트 진입점 + `analysis/` 패키지 모듈" 컨벤션을 따른다. 새 모듈 `analysis/rag_pipeline.py`가 Qdrant 하이브리드 검색과 프롬프트 조립을 담당하고, 기존 `analysis/pipeline.py`의 `list_analyzable_keywords()`/`analyze()`와 `embedding/encoder.py`의 `encode_batch()`, `embedding/pipeline.py`의 `_to_sparse_vector()`를 재사용해 중복 코드를 만들지 않는다.

**Tech Stack:** Python, `qdrant-client`(하이브리드 검색 — `query_points`/`Prefetch`/`FusionQuery`), `ollama`(LLM 호출, 기존 재사용), `FlagEmbedding`(BGE-M3 인코딩, 기존 재사용), MongoDB(`pymongo`, 기존 재사용).

## Global Constraints

- 이 프로젝트에는 자동화된 테스트(pytest 등)가 없다 — 기존 스크립트들과 동일하게, 각 함수는 `python -c` 스니펫으로 실 데이터에 대해 수동 검증하고, 최종적으로 `python rag_main.py`를 직접 실행해 눈으로 확인한다.
- 기존 `analysis/pipeline.py`, `analyze_main.py`는 수정하지 않는다 — 완전히 별도의 새 도구로 병존시킨다.
- 새 provider 추상화 계층을 만들지 않는다 — LLM 호출은 기존 `analysis.pipeline.analyze(prompt)`를 그대로 재사용한다.
- Qdrant 필터 파라미터명은 `query_points()`의 최상위 레벨에서는 `query_filter`, `Prefetch(...)` 내부에서는 `filter`다(설치된 `qdrant-client` 기준 확인됨 — 이름이 다르므로 헷갈리지 않게 주의).

---

### Task 1: RAG 설정값 추가

**Files:**
- Modify: `config/config_cilent.py` (파일 끝, `ANALYSIS_PROMPT_PATH = "analysis/prompt_template.md"` 다음 줄에 추가)

**Interfaces:**
- Produces: `RAG_TOP_K: int`, `RAG_PROMPT_PATH: str` — Task 3/4/5에서 import해서 사용.

- [ ] **Step 1: 설정값 추가**

`config/config_cilent.py` 파일 맨 끝에 다음을 추가한다:

```python

# RAG 질의응답 설정
RAG_TOP_K = 5
RAG_PROMPT_PATH = "analysis/rag_prompt_template.md"
```

- [ ] **Step 2: 값이 제대로 import되는지 확인**

Run: `.venv/Scripts/python.exe -c "from config.config_cilent import RAG_TOP_K, RAG_PROMPT_PATH; print(RAG_TOP_K, RAG_PROMPT_PATH)"`
Expected: `5 analysis/rag_prompt_template.md` 출력, 에러 없음

- [ ] **Step 3: Commit**

```bash
git add config/config_cilent.py
git commit -m "feat: add RAG config constants"
```

---

### Task 2: RAG 프롬프트 템플릿 작성

**Files:**
- Create: `analysis/rag_prompt_template.md`

**Interfaces:**
- Produces: `{keyword}`, `{context}`, `{question}` 플레이스홀더를 가진 템플릿 파일. Task 4의 `build_rag_prompt()`가 이 파일을 읽어서 `.format()`으로 채운다.

- [ ] **Step 1: 템플릿 파일 작성**

`analysis/rag_prompt_template.md`:

```
다음은 밈/신조어 "{keyword}"에 대해 수집된 자료 중, 아래 질문과 관련성이 높은 것들입니다.

{context}

위 자료를 근거로 다음 질문에 답변하세요. 자료에 없는 내용은 추측하지 말고 모른다고 답하세요.

질문: {question}
```

- [ ] **Step 2: 파일이 정상적으로 읽히는지 확인**

Run: `.venv/Scripts/python.exe -c "print(open('analysis/rag_prompt_template.md', encoding='utf-8').read())"`
Expected: 위 템플릿 내용이 그대로 출력됨

- [ ] **Step 3: Commit**

```bash
git add analysis/rag_prompt_template.md
git commit -m "feat: add RAG prompt template"
```

---

### Task 3: `analysis/rag_pipeline.py` — 하이브리드 검색 + 프롬프트 조립

**Files:**
- Create: `analysis/rag_pipeline.py`

**Interfaces:**
- Consumes:
  - `DB.drant_clitent.client` (기존 `QdrantClient` 인스턴스)
  - `embedding.pipeline._to_sparse_vector(lexical_weights: dict) -> models.SparseVector` (기존 함수 재사용)
  - `config.config_cilent.{QDRANT_COLLECTION, QDRANT_DENSE_VECTOR_NAME, QDRANT_SPARSE_VECTOR_NAME, RAG_TOP_K, RAG_PROMPT_PATH}`
- Produces:
  - `search_relevant_chunks(keyword: str, dense_vec: list[float], sparse: dict[str, float], top_k: int = RAG_TOP_K) -> list` — Task 5(`rag_main.py`)가 호출.
  - `build_rag_prompt(keyword: str, question: str, points: list) -> str` — Task 5가 호출. `points`는 `search_relevant_chunks()`가 반환한 것과 같은 타입(각 원소는 `.payload` 딕셔너리를 가진 Qdrant 포인트 객체).

- [ ] **Step 1: `search_relevant_chunks()` 작성**

`analysis/rag_pipeline.py`:

```python
"""
rag_pipeline.py
질문 임베딩을 Qdrant에서 하이브리드(dense+sparse) 검색해 관련 청크를 찾고,
그 청크를 근거로 LLM에 넘길 프롬프트를 조립한다.
"""

from qdrant_client.http import models

from config.config_cilent import (
    QDRANT_COLLECTION,
    QDRANT_DENSE_VECTOR_NAME,
    QDRANT_SPARSE_VECTOR_NAME,
    RAG_PROMPT_PATH,
    RAG_TOP_K,
)
from DB.drant_clitent import client
from embedding.pipeline import _to_sparse_vector


def search_relevant_chunks(
    keyword: str,
    dense_vec: list[float],
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword로 필터링한 뒤,
    dense+sparse 하이브리드 검색(RRF fusion)으로 상위 top_k개 포인트를 반환.
    """
    keyword_filter = models.Filter(
        must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    )

    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        prefetch=[
            models.Prefetch(
                query=dense_vec,
                using=QDRANT_DENSE_VECTOR_NAME,
                filter=keyword_filter,
                limit=top_k * 2,
            ),
            models.Prefetch(
                query=_to_sparse_vector(sparse),
                using=QDRANT_SPARSE_VECTOR_NAME,
                filter=keyword_filter,
                limit=top_k * 2,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=keyword_filter,
        limit=top_k,
        with_payload=True,
    )
    return response.points
```

- [ ] **Step 2: 실 데이터로 `search_relevant_chunks()` 동작 확인**

먼저 임베딩된 키워드가 하나라도 있는지 확인:

Run: `.venv/Scripts/python.exe -c "from analysis.pipeline import list_analyzable_keywords; print(list_analyzable_keywords())"`
Expected: 키워드 리스트 출력 (예: `['야르']`). **빈 리스트가 나오면** `embed_main.py`를 먼저 실행해 최소 한 키워드를 임베딩해둔 뒤 이 단계를 진행한다.

위에서 나온 키워드 중 하나(`<키워드>`)를 사용해서:

Run:
```bash
.venv/Scripts/python.exe -c "
from embedding.encoder import encode_batch
from analysis.rag_pipeline import search_relevant_chunks

dense_vecs, lexical_weights = encode_batch(['이 밈은 무슨 뜻이야?'])
points = search_relevant_chunks('<키워드>', dense_vecs[0], lexical_weights[0], top_k=3)
print(len(points))
for p in points:
    print(p.score, p.payload.get('title'), p.payload.get('text', '')[:50])
"
```
Expected: `len(points)`가 0보다 크고(최대 3), 각 줄에 score/title/text 일부가 출력됨. 에러(특히 `query_filter`/`filter` 파라미터명 오타로 인한 `ValidationError`) 없이 실행되어야 함.

- [ ] **Step 3: `build_rag_prompt()` 작성**

같은 파일 `analysis/rag_pipeline.py` 끝에 이어서 추가:

```python
_CONTEXT_SEPARATOR = "\n\n---\n\n"


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

- [ ] **Step 4: `build_rag_prompt()` 동작 확인**

Step 2에서 얻은 `points`를 이어서 사용:

Run:
```bash
.venv/Scripts/python.exe -c "
from embedding.encoder import encode_batch
from analysis.rag_pipeline import search_relevant_chunks, build_rag_prompt

dense_vecs, lexical_weights = encode_batch(['이 밈은 무슨 뜻이야?'])
points = search_relevant_chunks('<키워드>', dense_vecs[0], lexical_weights[0], top_k=3)
prompt = build_rag_prompt('<키워드>', '이 밈은 무슨 뜻이야?', points)
print(prompt)
"
```
Expected: 템플릿의 `{keyword}`/`{context}`/`{question}` 자리에 실제 값이 채워진 프롬프트 전문이 출력됨. `{`/`}`가 그대로 남아있으면 안 됨.

- [ ] **Step 5: Commit**

```bash
git add analysis/rag_pipeline.py
git commit -m "feat: add hybrid search and prompt assembly for RAG"
```

---

### Task 4: `rag_main.py` 진입점 작성

**Files:**
- Create: `rag_main.py`

**Interfaces:**
- Consumes:
  - `analysis.pipeline.list_analyzable_keywords() -> list[str]` (기존)
  - `analysis.pipeline.analyze(prompt: str) -> str` (기존)
  - `embedding.encoder.encode_batch(texts: list[str]) -> tuple[list[list[float]], list[dict]]` (기존)
  - `analysis.rag_pipeline.search_relevant_chunks(...)`, `analysis.rag_pipeline.build_rag_prompt(...)` (Task 3)

- [ ] **Step 1: `rag_main.py` 작성**

`rag_main.py` (리포 루트, 기존 `analyze_main.py`와 동일한 스타일):

```python
"""
rag_main.py
밈/신조어 키워드를 고르고 자유 텍스트로 질문하면, Qdrant 하이브리드 검색으로
관련 청크를 찾아 그걸 근거로 로컬 LLM(Ollama)이 답변한다.

main.py / analyze_main.py와 동일하게 루트에서 바로 실행 가능:
    python rag_main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from analysis.pipeline import analyze, list_analyzable_keywords
from analysis.rag_pipeline import build_rag_prompt, search_relevant_chunks
from embedding.encoder import encode_batch

if __name__ == "__main__":
    keywords = list_analyzable_keywords()
    if not keywords:
        print("분석할 수 있는 키워드가 없습니다.")
        sys.exit(1)

    print("검색 가능한 키워드:")
    for i, keyword in enumerate(keywords, start=1):
        print(f"  {i}. {keyword}")

    raw_choice = input("키워드 선택 (번호 입력): ").strip()
    if not raw_choice.isdigit() or not (1 <= int(raw_choice) <= len(keywords)):
        print("잘못된 선택입니다.")
        sys.exit(1)

    selected_keyword = keywords[int(raw_choice) - 1]

    question = input("질문을 입력하세요: ").strip()
    if not question:
        print("질문을 입력하세요.")
        sys.exit(1)

    print("[검색 중] 관련 청크 조회...")
    dense_vecs, lexical_weights = encode_batch([question])
    points = search_relevant_chunks(selected_keyword, dense_vecs[0], lexical_weights[0])
    if not points:
        print("검색 결과가 없습니다.")
        sys.exit(1)
    print(f"[검색 완료] 관련 청크 {len(points)}개 발견")

    prompt = build_rag_prompt(selected_keyword, question, points)

    print("[답변 생성 중] LLM에게 질의 중...")
    answer = analyze(prompt)

    print()
    print("=" * 40)
    print(f"질문: {question}")
    print("=" * 40)
    print(answer)
    print()
    print("-- 근거 출처 --")
    for point in points:
        title = point.payload.get("title") or "제목 없음"
        url = point.payload.get("url") or "출처 없음"
        print(f"  - {title} ({url})")
```

- [ ] **Step 2: 문법 오류 없이 import되는지 확인**

Run: `.venv/Scripts/python.exe -c "import ast; ast.parse(open('rag_main.py', encoding='utf-8').read())"`
Expected: 에러 없이 조용히 종료 (문법 오류가 있으면 `SyntaxError` 출력)

- [ ] **Step 3: Commit**

```bash
git add rag_main.py
git commit -m "feat: add rag_main.py RAG entry point"
```

---

### Task 5: End-to-end 수동 검증

**Files:**
- (없음 — 실행 검증만)

- [ ] **Step 1: 전체 흐름 실행**

Ollama가 로컬에서 실행 중인지 먼저 확인(`ollama list` 등으로 `qwen3:latest`가 있는지). 그다음:

Run: `.venv/Scripts/python.exe rag_main.py`

입력 순서: 키워드 번호 선택 → 질문 입력(예: "이 밈은 언제 쓰여?")

Expected:
1. 키워드 목록이 번호와 함께 출력됨
2. "[검색 중] 관련 청크 조회..." 후 "[검색 완료] 관련 청크 N개 발견" (N ≤ 5)
3. "[답변 생성 중]" 후 LLM 답변 텍스트가 출력됨
4. "-- 근거 출처 --" 아래 title/url 목록이 출력됨 (검색된 청크 수만큼)
5. 에러 없이 정상 종료

- [ ] **Step 2: 검색 결과가 실제로 질문과 관련 있는지 육안 확인**

출력된 근거 청크의 `text`(Task 3 Step 2/4에서 확인한 방식으로 다시 조회 가능)가 질문과 무관한 내용이면, `RAG_TOP_K`를 늘리거나 하이브리드 fusion 방식을 재검토할 필요가 있음을 기록해두되, 이번 계획 범위에서는 수정하지 않는다(설계 문서의 "스코프에서 제외한 것" 참고).

- [ ] **Step 3: 잘못된 입력에 대한 에러 처리 확인**

Run: `.venv/Scripts/python.exe rag_main.py` 다시 실행 후, 키워드 번호에 범위를 벗어난 값(예: `999`) 입력
Expected: "잘못된 선택입니다." 출력 후 종료 (트레이스백 없음)


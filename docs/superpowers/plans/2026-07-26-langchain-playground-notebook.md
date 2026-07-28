# LangChain 실험용 노트북 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 질문/검색 파라미터/프롬프트/모델 설정을 셀 단위로 바꿔가며 LLM 답변 변화와 벡터 검색(dense/sparse/RRF) 결과를 비교 실험할 수 있는 `analysis/langchain_playground.ipynb`를 추가한다.

**Architecture:** 기존 `analysis/pipeline.py`(키워드 목록), `embedding/encoder.py`(임베딩), `analysis/rag_pipeline.py`(검색)를 그대로 재사용하고, 여기에 dense-only/sparse-only 검색 함수 2개만 추가한다. 노트북은 이 함수들을 셀 단위로 호출하며, LLM 호출은 `ChatNVIDIA`를 노트북에서 직접 생성해 프롬프트/모델 설정을 자유롭게 바꿀 수 있게 한다. API 키가 하드코딩되어 git 히스토리에 남아있는 기존 `analysis/qwen_nim.ipynb`는 삭제한다.

**Tech Stack:** Python, LangChain (`langchain-nvidia-ai-endpoints`), Qdrant, MongoDB, BGE-M3(FlagEmbedding), Jupyter/nbformat 4.

## Global Constraints

- 자동화된 pytest 테스트가 이 프로젝트에는 없다 — 각 태스크의 검증은 실제 인프라(Qdrant/MongoDB 실행 중, 최소 1개 키워드가 임베딩 완료된 상태)에 대고 스크립트를 실행해 눈으로 결과를 확인하는 방식이다.
- API 키를 코드/노트북에 하드코딩하지 않는다 — `config.config_cilent.NIM_KEY`(`.env`의 `NIM_KEY`)를 재사용한다.
- `rag_main.py`, `analysis/pipeline.py`, `embedding/encoder.py`, `config/config_cilent.py`는 수정하지 않는다(이번 변경 범위 밖).
- 새 검색 로직(dense-only/sparse-only)은 노트북에 직접 쓰지 않고 `analysis/rag_pipeline.py`에 함수로 추가한다.
- 노트북의 각 코드 셀 앞에는 "이 셀이 하는 일"과 "실험하려면 무엇을 바꾸면 되는지"를 설명하는 마크다운 셀을 둔다.
- 참고 스펙: `docs/superpowers/specs/2026-07-26-langchain-playground-notebook-design.md`

---

### Task 1: `analysis/rag_pipeline.py`에 dense-only / sparse-only 검색 함수 추가

**Files:**
- Modify: `analysis/rag_pipeline.py`

**Interfaces:**
- Consumes: 기존 `client`(`DB.drant_clitent`), `_to_sparse_vector`(`embedding.pipeline`), `QDRANT_COLLECTION`/`QDRANT_DENSE_VECTOR_NAME`/`QDRANT_SPARSE_VECTOR_NAME`/`RAG_TOP_K`(`config.config_cilent`) — 모두 이미 이 파일에 import되어 있음.
- Produces: `search_dense_only(keyword: str, dense_vec: list[float], top_k: int = RAG_TOP_K) -> list[models.ScoredPoint]`, `search_sparse_only(keyword: str, sparse: dict[str, float], top_k: int = RAG_TOP_K) -> list[models.ScoredPoint]` — Task 3(노트북)에서 이 두 함수와 기존 `search_relevant_chunks`를 나란히 호출한다.

- [ ] **Step 1: 함수 추가**

`analysis/rag_pipeline.py`에서 아래 문자열을 찾는다:

```python
    return response.points


_CONTEXT_SEPARATOR = "\n\n---\n\n"
```

이것을 아래로 교체한다(새 함수 2개를 `search_relevant_chunks`와 `_CONTEXT_SEPARATOR` 사이에 삽입):

```python
    return response.points


def search_dense_only(
    keyword: str,
    dense_vec: list[float],
    top_k: int = RAG_TOP_K,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword로 필터링한 뒤,
    dense 벡터만으로(sparse/융합 없이) 상위 top_k개 포인트를 반환.
    순수 의미 유사도 검색 결과만 확인하고 싶을 때 사용한다.
    """
    keyword_filter = models.Filter(
        must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    )

    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=dense_vec,
        using=QDRANT_DENSE_VECTOR_NAME,
        query_filter=keyword_filter,
        limit=top_k,
        with_payload=True,
    )
    return response.points


def search_sparse_only(
    keyword: str,
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
) -> list[models.ScoredPoint]:
    """
    Qdrant mimori_chunks에서 payload.keyword == keyword로 필터링한 뒤,
    sparse(lexical) 벡터만으로(dense/융합 없이) 상위 top_k개 포인트를 반환.
    단어 일치 기반 유사도 검색 결과만 확인하고 싶을 때 사용한다.
    """
    keyword_filter = models.Filter(
        must=[models.FieldCondition(key="keyword", match=models.MatchValue(value=keyword))]
    )

    response = client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=_to_sparse_vector(sparse),
        using=QDRANT_SPARSE_VECTOR_NAME,
        query_filter=keyword_filter,
        limit=top_k,
        with_payload=True,
    )
    return response.points


_CONTEXT_SEPARATOR = "\n\n---\n\n"
```

- [ ] **Step 2: 문법 검증**

Run: `python -c "import ast; ast.parse(open('analysis/rag_pipeline.py', encoding='utf-8').read())"`
Expected: 에러 없이 종료 (출력 없음).

- [ ] **Step 3: 실제 인프라에 대고 동작 확인**

전제조건: Qdrant/MongoDB가 실행 중이고, `embed_main.py`로 최소 1개 키워드가 임베딩 완료된 상태여야 한다.

Run:
```bash
python -c "
from analysis.pipeline import list_analyzable_keywords
from analysis.rag_pipeline import search_dense_only, search_sparse_only, search_relevant_chunks
from embedding.encoder import encode_batch, unload_model

keywords = list_analyzable_keywords()
assert keywords, '임베딩된 키워드가 없습니다 - embed_main.py를 먼저 실행하세요'
keyword = keywords[0]
print('테스트 키워드:', keyword)

dense_vecs, lexical_weights = encode_batch(['테스트 질문'])
unload_model()

dense_points = search_dense_only(keyword, dense_vecs[0], 3)
sparse_points = search_sparse_only(keyword, lexical_weights[0], 3)
fused_points = search_relevant_chunks(keyword, dense_vecs[0], lexical_weights[0], 3)

print('dense:', len(dense_points))
print('sparse:', len(sparse_points))
print('fused:', len(fused_points))
for p in dense_points + sparse_points + fused_points:
    assert hasattr(p, 'score') and hasattr(p, 'payload')
print('OK')
"
```
Expected: `dense:`, `sparse:`, `fused:` 각각 0 이상의 정수 출력, 마지막 줄 `OK`. 예외 없이 종료.

- [ ] **Step 4: Commit**

```bash
git add analysis/rag_pipeline.py
git commit -m "feat: rag_pipeline에 dense-only/sparse-only 검색 함수 추가"
```

---

### Task 2: 유출된 API 키가 포함된 `analysis/qwen_nim.ipynb` 삭제

**Files:**
- Delete: `analysis/qwen_nim.ipynb`

**Interfaces:**
- Consumes: 없음.
- Produces: 없음 (파일 삭제만 수행).

- [ ] **Step 1: 파일 삭제**

```bash
git rm analysis/qwen_nim.ipynb
```

- [ ] **Step 2: 삭제 확인**

Run: `git status --short`
Expected: `D  analysis/qwen_nim.ipynb` 라인이 보임.

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: API 키가 하드코딩된 qwen_nim.ipynb 삭제 (langchain_playground.ipynb로 대체)"
```

- [ ] **Step 4: (사람이 직접 수행 — 자동화 불가) NVIDIA 콘솔에서 키 재발급**

파일 삭제만으로는 git 히스토리(커밋 63b17bb)에 남은 키가 무효화되지 않는다. NVIDIA 콘솔(https://build.nvidia.com 등 API 키 발급 페이지)에 로그인해 `nvapi-dygq...vh7J`(전체 값은 커밋 63b17bb의 `analysis/qwen_nim.ipynb` 참고 — 이 문서에는 재유출 방지를 위해 전체 키를 적지 않는다) 키를 revoke하고 새 키를 발급받아 `.env`의 `NIM_KEY`를 갱신한다. 이 단계는 코드 변경이 아니므로 커밋 대상이 아니다.

---

### Task 3: `analysis/langchain_playground.ipynb` 생성

**Files:**
- Create: `analysis/langchain_playground.ipynb`

**Interfaces:**
- Consumes: `list_analyzable_keywords`(Task 이전부터 존재, `analysis.pipeline`), `search_relevant_chunks`/`search_dense_only`/`search_sparse_only`(Task 1, `analysis.rag_pipeline`), `encode_batch`/`unload_model`(`embedding.encoder`), `NIM_KEY`(`config.config_cilent`), `ChatNVIDIA`(`langchain_nvidia_ai_endpoints`).
- Produces: 없음 (최종 산출물, 다른 태스크가 이 파일을 참조하지 않음).

- [ ] **Step 1: 노트북 파일 작성**

아래 내용 그대로 `analysis/langchain_playground.ipynb`를 생성한다(UTF-8, 유효한 nbformat 4 JSON).

```json
{
 "cells": [
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "# LangChain 실험 플레이그라운드\n",
    "\n",
    "밈/신조어 키워드에 대해 질문 -> 벡터 검색(dense/sparse/융합) -> 프롬프트 조립 -> LLM 답변까지, 각 셀을 독립적으로 재실행하며 실험하기 위한 노트북입니다.\n",
    "\n",
    "아래 셀들을 순서대로 한 번 실행한 뒤, 3번(질문) ~ 7번(LLM 호출) 셀만 값을 바꿔가며 반복 재실행하면 됩니다."
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "## 1. 환경 설정\n",
    "\n",
    "**이 셀이 하는 일**: OS 환경변수, 인코딩, import를 준비합니다.\n",
    "\n",
    "**바꿀 것**: 없음 — 노트북을 열면 한 번만 실행하면 됩니다."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "import sys\n",
    "import os\n",
    "\n",
    "os.environ.setdefault(\"KMP_DUPLICATE_LIB_OK\", \"TRUE\")\n",
    "\n",
    "# 이 노트북은 analysis/ 안에 있으므로 커널 작업 디렉토리는 analysis/ 입니다.\n",
    "# 프로젝트 루트(analysis/의 상위 폴더)를 import 경로에 추가합니다.\n",
    "PROJECT_ROOT = os.path.abspath(\"..\")\n",
    "if PROJECT_ROOT not in sys.path:\n",
    "    sys.path.insert(0, PROJECT_ROOT)\n",
    "\n",
    "if sys.platform == \"win32\":\n",
    "    sys.stdin.reconfigure(encoding=\"utf-8\")\n",
    "    sys.stdout.reconfigure(encoding=\"utf-8\")\n",
    "    sys.stderr.reconfigure(encoding=\"utf-8\")\n",
    "\n",
    "from analysis.pipeline import list_analyzable_keywords\n",
    "from analysis.rag_pipeline import (\n",
    "    search_relevant_chunks,\n",
    "    search_dense_only,\n",
    "    search_sparse_only,\n",
    ")\n",
    "from embedding.encoder import encode_batch, unload_model\n",
    "from config.config_cilent import NIM_KEY\n",
    "from langchain_nvidia_ai_endpoints import ChatNVIDIA\n",
    "\n",
    "print(\"설정 완료\")"
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "## 2. 키워드 선택\n",
    "\n",
    "**이 셀이 하는 일**: MongoDB에서 임베딩이 끝난 밈 키워드 목록을 가져와 보여줍니다.\n",
    "\n",
    "**실험하려면**: 출력된 목록 중 하나를 골라 `KEYWORD` 변수에 문자열로 직접 대입한 뒤 재실행하세요."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "keywords = list_analyzable_keywords()\n",
    "print(f\"분석 가능한 키워드 {len(keywords)}개:\")\n",
    "for kw in keywords:\n",
    "    print(f\"  - {kw}\")\n",
    "\n",
    "KEYWORD = keywords[0] if keywords else \"\"\n",
    "print(f\"\\n현재 선택된 KEYWORD = {KEYWORD!r}\")"
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "## 3. 질문 입력 + 임베딩\n",
    "\n",
    "**이 셀이 하는 일**: 자유 텍스트 질문을 BGE-M3로 dense 벡터 + sparse(lexical) 벡터로 변환합니다.\n",
    "\n",
    "**실험하려면**: `QUESTION` 문자열만 바꾸면 됩니다. 질문이 바뀌면 검색되는 청크가 달라지므로, 이 셀부터 다시 실행해야 아래 결과에 반영됩니다. (임베딩 모델은 재실행마다 새로 로드하지 않도록 그대로 유지합니다 — LLM 호출은 NVIDIA API라 로컬 GPU와 충돌하지 않습니다.)"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "QUESTION = \"이 밈은 왜 유행했나요?\"\n",
    "\n",
    "dense_vecs, lexical_weights = encode_batch([QUESTION])\n",
    "dense_vec, sparse = dense_vecs[0], lexical_weights[0]\n",
    "\n",
    "print(f\"QUESTION = {QUESTION!r}\")\n",
    "print(f\"dense_vec 길이 = {len(dense_vec)}\")\n",
    "print(f\"sparse 항목 수 = {len(sparse)}\")"
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "## 4. 검색 파라미터 + 실행\n",
    "\n",
    "**이 셀이 하는 일**: 같은 질문 벡터로 dense만 / sparse만 / RRF 융합, 세 가지 방식으로 Qdrant에서 관련 청크를 검색합니다.\n",
    "\n",
    "**실험하려면**: `TOP_K`를 늘리거나 줄여서 더 많은/적은 근거 청크를 가져와볼 수 있습니다."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "TOP_K = 5\n",
    "\n",
    "dense_points = search_dense_only(KEYWORD, dense_vec, TOP_K)\n",
    "sparse_points = search_sparse_only(KEYWORD, sparse, TOP_K)\n",
    "fused_points = search_relevant_chunks(KEYWORD, dense_vec, sparse, TOP_K)\n",
    "\n",
    "print(f\"dense={len(dense_points)}, sparse={len(sparse_points)}, fused={len(fused_points)}\")"
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "## 5. 검색 결과 확인\n",
    "\n",
    "**이 셀이 하는 일**: dense / sparse / 융합(RRF) 검색 결과를 나란히 비교 출력합니다(점수, 제목, 출처, 본문 일부). 벡터 검색이 실제로 어떤 데이터를 가져오는지 여기서 확인할 수 있습니다.\n",
    "\n",
    "**실험하려면**: 이 셀 자체는 결과를 출력만 하므로 수정할 것이 없습니다 — 위 셀들(질문, TOP_K, 키워드)을 바꾸고 재실행해서 차이를 비교하세요."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "def _print_points(label, points):\n",
    "    print(f\"--- {label} ({len(points)}개) ---\")\n",
    "    for p in points:\n",
    "        title = p.payload.get(\"title\") or \"제목 없음\"\n",
    "        url = p.payload.get(\"url\") or \"출처 없음\"\n",
    "        text = p.payload.get(\"text\", \"\")\n",
    "        print(f\"  score={p.score:.4f} | {title} ({url})\")\n",
    "        print(f\"  {text[:150]}\")\n",
    "    print()\n",
    "\n",
    "_print_points(\"DENSE\", dense_points)\n",
    "_print_points(\"SPARSE\", sparse_points)\n",
    "_print_points(\"FUSED(RRF)\", fused_points)"
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "## 6. 프롬프트 작성\n",
    "\n",
    "**이 셀이 하는 일**: 검색된 청크(`fused_points`)를 근거 자료로 넣어 LLM에게 실제로 전달할 프롬프트를 완성합니다.\n",
    "\n",
    "**실험하려면**: `PROMPT_TEMPLATE` 문자열을 자유롭게 수정하세요(지시문 추가, 어조 변경, 출력 형식 지정 등). `{keyword}` / `{context}` / `{question}` 자리표시자 세 개는 반드시 그대로 남겨둬야 합니다."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "PROMPT_TEMPLATE = \"\"\"당신은 밈/신조어 분석 전문가입니다.\n",
    "\n",
    "키워드: {keyword}\n",
    "\n",
    "자료:\n",
    "{context}\n",
    "\n",
    "질문: {question}\n",
    "\n",
    "위 자료를 근거로 답변하세요. 자료에 없는 내용은 추측하지 말고 모른다고 답하세요.\n",
    "\"\"\"\n",
    "\n",
    "context = \"\\n\\n---\\n\\n\".join(\n",
    "    f\"[출처: {p.payload.get('title') or '제목 없음'} / {p.payload.get('url') or '출처 없음'}]\\n{p.payload.get('text', '')}\"\n",
    "    for p in fused_points\n",
    ")\n",
    "prompt = PROMPT_TEMPLATE.format(keyword=KEYWORD, context=context, question=QUESTION)\n",
    "print(prompt)"
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": [
    "## 7. LLM 호출\n",
    "\n",
    "**이 셀이 하는 일**: 완성된 프롬프트를 NVIDIA API(ChatNVIDIA)로 보내고 답변을 받아 출력합니다.\n",
    "\n",
    "**실험하려면**: `MODEL` / `TEMPERATURE` / `TOP_P` 값을 바꿔서, 같은 프롬프트에도 답변이 어떻게 달라지는지 비교해보세요."
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "MODEL = \"deepseek-ai/deepseek-v4-flash\"\n",
    "TEMPERATURE = 1\n",
    "TOP_P = 0.95\n",
    "\n",
    "llm_client = ChatNVIDIA(\n",
    "    model=MODEL,\n",
    "    api_key=NIM_KEY,\n",
    "    temperature=TEMPERATURE,\n",
    "    top_p=TOP_P,\n",
    "    max_completion_tokens=16384,\n",
    "    timeout=6000,\n",
    ")\n",
    "\n",
    "response = llm_client.invoke([{\"role\": \"user\", \"content\": prompt}])\n",
    "print(response.content)"
   ]
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "Python 3",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "name": "python"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 5
}
```

- [ ] **Step 2: JSON 유효성 검증**

Run: `python -c "import json; nb = json.load(open('analysis/langchain_playground.ipynb', encoding='utf-8')); print(len(nb['cells']), 'cells')"`
Expected: `15 cells` 출력 (마크다운 8개 + 코드 7개).

- [ ] **Step 3: 셀 내용 스모크 테스트 (구문 오류만 확인, 실행은 안 함)**

Run:
```bash
python -c "
import json, ast
nb = json.load(open('analysis/langchain_playground.ipynb', encoding='utf-8'))
for i, cell in enumerate(nb['cells']):
    if cell['cell_type'] == 'code':
        src = ''.join(cell['source'])
        ast.parse(src)
print('모든 코드 셀 구문 정상')
"
```
Expected: `모든 코드 셀 구문 정상` 출력, 예외 없음.

- [ ] **Step 4: (전제조건 충족 시) Jupyter에서 전체 실행으로 기능 검증**

전제조건: Qdrant/MongoDB 실행 중, 최소 1개 키워드 임베딩 완료, `.env`에 유효한 `NIM_KEY` 설정됨.

`jupyter lab` (또는 IDE의 Jupyter 확장)으로 `analysis/langchain_playground.ipynb`를 열고 "Run All"을 실행한다.

Expected: 7개 코드 셀이 순서대로 에러 없이 실행되고, 5번 셀에서 DENSE/SPARSE/FUSED(RRF) 세 그룹의 청크가 각각 출력되며, 7번 셀에서 LLM 답변 텍스트가 출력된다. `QUESTION`을 다른 문장으로 바꿔 3~7번 셀만 재실행했을 때 5번 셀의 검색 결과와 7번 셀의 답변이 바뀌는 것을 확인한다.

- [ ] **Step 5: Commit**

```bash
git add analysis/langchain_playground.ipynb
git commit -m "feat: 입력 데이터별 LLM 답변 변화를 실험하는 langchain_playground 노트북 추가"
```

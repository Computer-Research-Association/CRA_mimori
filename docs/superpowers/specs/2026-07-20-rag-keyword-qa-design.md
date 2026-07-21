# RAG 기반 키워드 질의응답 기능 설계 (`analysis/rag_pipeline.py`)

## 목적

기존 `analyze_main.py`는 키워드를 고르면 그 키워드의 청크 전체를(벡터 유사도 검색 없이) LLM에 넘겨 고정된 4가지 항목을 분석하는 도구다. 이번에 추가하는 기능은 이것과 별개로, 사용자가 **자유 텍스트 질문**을 입력하면 그 질문을 임베딩해 Qdrant에서 **벡터 유사도(하이브리드 dense+sparse) top-k 검색**으로 관련 청크만 뽑고, 그 청크를 근거로 LLM이 답변하는 **진짜 RAG 질의응답 도구**를 추가한다.

## 아키텍처

기존 파이프라인 스크립트 컨벤션(`main.py`, `preprocess_main.py`, `embed_main.py`, `analyze_main.py`)을 그대로 따라 새 진입점 스크립트를 추가한다. 기존 `analyze_main.py` / `analysis/pipeline.py`는 건드리지 않고 그대로 재사용한다.

```
rag_main.py                    # 진입점 (python rag_main.py)
analysis/
  rag_pipeline.py              # 신규 — 하이브리드 검색, RAG 프롬프트 조립
  rag_prompt_template.md       # 신규 — 질문+근거청크를 채우는 프롬프트 템플릿
  pipeline.py                  # 기존 그대로 — list_analyzable_keywords()/analyze() 재사용
```

`analysis/pipeline.py`의 `analyze(prompt, model=ANALYSIS_MODEL)`는 이미 "프롬프트 문자열을 받아 LLM 응답 텍스트를 반환"하는 형태로 provider 세부사항을 감추고 있다. 이 함수를 그대로 재사용하므로, 나중에 LLM provider를 Ollama에서 NVIDIA API 등으로 바꾸더라도 `analyze()` 내부만 수정하면 되고 `rag_pipeline.py`/`rag_main.py`는 변경할 필요가 없다 — 별도의 provider 추상화 계층을 새로 만들지 않는다.

## 데이터 흐름

1. `rag_main.py` 실행 → `pipeline.list_analyzable_keywords()`(기존 재사용)로 `memes.is_embedded=True`인 키워드 목록을 번호와 함께 출력
2. 사용자가 번호로 키워드 하나 선택
3. 사용자가 질문을 자유 텍스트로 입력
4. `embedding/encoder.py`의 `encode_batch([question])`(기존 재사용)로 질문의 dense 벡터 + sparse(lexical weights) 벡터를 얻음
5. `rag_pipeline.search_relevant_chunks(keyword, dense_vec, sparse, top_k)`
   - Qdrant `query_points()`를 사용해 dense/sparse 두 벡터를 각각 `prefetch`로 넣고 `Fusion.RRF`로 결합하는 하이브리드 검색 수행
   - `payload.keyword == 선택한 키워드`로 필터링해 해당 키워드 범위 내에서만 검색
   - 상위 `RAG_TOP_K`(기본 5)개 포인트(payload 포함)를 점수 순으로 반환
6. `rag_pipeline.build_rag_prompt(question, points)` → `analysis/rag_prompt_template.md`를 읽어 `{question}`, `{context}` 플레이스홀더를 채움. `{context}`는 각 청크를 `[출처: {title} / {url}]\n{text}` 형태로 만들어 구분자로 이어붙인 문자열
7. `pipeline.analyze(prompt)`(기존 재사용) → Ollama(`qwen3:latest`) 호출, 답변 텍스트 반환
8. `rag_main.py`가 답변 텍스트와, 근거로 쓰인 각 청크의 출처(title/url) 목록을 함께 터미널에 출력. DB에 결과를 저장하는 기능은 없음(순수 조회 도구)

## 컴포넌트 상세

### `analysis/rag_pipeline.py`

```python
def search_relevant_chunks(
    keyword: str,
    dense_vec: list[float],
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
) -> list[models.ScoredPoint]:
    """Qdrant mimori_chunks에서 keyword로 필터링한 뒤, dense+sparse 하이브리드
    검색(RRF fusion)으로 상위 top_k개 포인트를 반환. payload(text, title, url 등) 포함."""

def build_rag_prompt(question: str, points: list[models.ScoredPoint]) -> str:
    """rag_prompt_template.md를 읽어 {question}/{context}를 채운 문자열 반환.
    {context}는 각 포인트의 payload를
    '[출처: {title} / {url}]\\n{text}' 형태로 만들어 구분자로 이어붙인 것."""
```

- `search_relevant_chunks`는 기존 `DB/drant_clitent.py`의 `client`를 재사용한다.
- 벡터 인코딩은 새로 만들지 않고 `embedding/encoder.py`의 `encode_batch()`를 그대로 재사용한다(질문 하나짜리 리스트로 호출).
- `encode_batch()`가 반환하는 raw lexical_weights(`{token_id(str): weight}`)를 Qdrant `SparseVector`로 변환하는 로직은 `embedding/pipeline.py`의 `_to_sparse_vector()`와 동일해야 한다. 이 함수를 그대로 import해 재사용한다(모듈 사설(`_` prefix) 함수를 다른 모듈에서 가져다 쓰는 형태가 어색하면, 구현 시점에 `embedding/encoder.py` 등 공용 위치로 옮기는 것도 가능 — 로직 중복은 만들지 않는다는 원칙만 지킨다).

### `analysis/rag_prompt_template.md` (초안)

```
다음은 밈/신조어 "{keyword}"에 대해 수집된 자료 중, 아래 질문과 관련성이 높은 것들입니다.

{context}

위 자료를 근거로 다음 질문에 답변하세요. 자료에 없는 내용은 추측하지 말고 모른다고 답하세요.

질문: {question}
```

### `rag_main.py`

`analyze_main.py`와 동일한 CLI 스타일(번호 선택 → 텍스트 입력 → 결과 출력)로 작성한다.

## 설정 추가

`config/config_cilent.py`에 추가:
```python
RAG_TOP_K = 5
RAG_PROMPT_PATH = "analysis/rag_prompt_template.md"
```

## 에러 처리

기존 스크립트들의 스타일(예외를 잡아 메시지 출력 후 종료, 재시도 로직 없음)을 그대로 따른다:

- 분석 가능한 키워드가 하나도 없음 → "분석할 수 있는 키워드가 없습니다" 출력 후 종료
- 사용자가 목록에 없는 번호 입력 → "잘못된 선택입니다" 출력 후 종료
- 질문을 빈 값으로 입력 → "질문을 입력하세요" 출력 후 종료
- 검색은 항상 `top_k`(또는 그보다 청크 수가 적으면 있는 만큼)를 반환한다 — 별도의 유사도 점수 임계값 필터링은 두지 않는다(YAGNI, 실제로 관련 없는 결과가 자주 섞이는 문제가 확인되면 그때 추가)
- 선택한 키워드에 청크가 아예 없음(드문 불일치) → "검색 결과가 없습니다" 출력 후 종료
- Qdrant/Ollama 호출 실패 → 예외 메시지 그대로 출력 후 종료

## 테스트 / 검증 방식

이 프로젝트에는 자동화된 테스트가 없다(기존 스크립트들과 동일). 구현 후 `python rag_main.py`를 직접 실행해 키워드 선택 → 질문 입력 → 답변+출처 출력까지 눈으로 확인하는 방식으로 검증한다.

## 스코프에서 제외한 것

- LLM provider 추상화 계층(예: provider별 클래스/인터페이스) 신설 — 기존 `analyze()` 재사용으로 이미 provider가 감춰져 있어 별도 계층이 불필요
- 유사도 점수 임계값 기반 결과 필터링 — 문제가 실제로 확인되기 전까지 만들지 않음
- 반복 질의(대화형 루프) — 이번 버전은 질문 1개 → 답변 → 종료의 단발성 실행만 지원
- 답변/검색 결과를 MongoDB 등에 다시 저장하는 기능 — 순수 조회 도구
- 키워드 범위를 벗어난 전체 데이터 검색 — 이번 버전은 키워드 선택 후 그 범위 내에서만 검색

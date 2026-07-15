# LLM 기반 밈 분석 기능 설계 (`feature/llminsert`)

## 목적

이미 크롤링 → 전처리/청킹 → 임베딩(Qdrant `mimori_chunks`) 파이프라인을 거친 밈/신조어 키워드 중 하나를 골라, 그 밈에 대해 수집된 원본 텍스트 전체를 로컬 LLM(Ollama)에게 보여주고 다음을 분석하게 하는 대화형 CLI 도구를 추가한다:

1. 이 밈은 언제, 어떤 상황에서 쓰이는가
2. 어떤 뉘앙스를 가지는가
3. 원래 뜻은 무엇이었는가
4. 그 뜻이 시간에 따라 어떻게 변화했는가

이 분석 기준(위 4가지 항목)은 향후 바뀔 수 있으므로, 코드가 아니라 별도 프롬프트 파일에서 직접 수정 가능해야 한다.

## 아키텍처

기존 파이프라인 스크립트 컨벤션(`main.py` = 크롤링, `preprocess_main.py` = 전처리, `embed_main.py` = 임베딩)을 그대로 따라 새 진입점 스크립트 하나를 추가한다.

```
analyze_main.py              # 진입점 (python analyze_main.py)
analysis/
  __init__.py
  pipeline.py                 # 키워드 목록 조회, 청크 조회, 프롬프트 조립, LLM 호출
  prompt_template.md           # 직접 보고 수정하는 분석 기준 프롬프트
```

## 데이터 흐름

1. `analyze_main.py` 실행 → `pipeline.list_analyzable_keywords()` 호출 → MongoDB `memes` 컬렉션에서 `is_embedded=True`인 문서들의 distinct `keyword`를 번호(1부터)와 함께 출력
2. 사용자가 **번호**를 입력해 키워드 하나 선택 (키워드 문자열 직접 입력은 받지 않음 — 목록에 표시된 번호만 유효한 입력)
3. `pipeline.fetch_keyword_chunks(keyword)` → Qdrant `mimori_chunks`에서 payload 필터 `keyword == 선택값`으로 **전체 스크롤**(scroll, 벡터 유사도 검색이 아닌 메타데이터 정확 매칭) → 해당 키워드의 모든 청크 텍스트 리스트 반환
   - 한 키워드 분량이 LLM 컨텍스트를 넘어갈 가능성에 대한 별도 제한/요약 로직은 두지 않는다 (qwen3 컨텍스트 윈도우가 충분히 크고, 실제로 넘치는 사례가 나오기 전에 미리 만들 필요 없음 — YAGNI)
4. `pipeline.build_prompt(keyword, chunks)` → `analysis/prompt_template.md`를 읽어 `{keyword}` / `{content}`(청크 전체를 이어붙인 텍스트) 플레이스홀더를 채움
5. `pipeline.analyze(prompt)` → `ollama.chat(model=ANALYSIS_MODEL, messages=[...])` 호출, 응답 텍스트 반환
   - LangChain(`langchain-ollama`)은 쓰지 않는다 — 단발성 프롬프트 조립 + 단일 호출이라 LangChain 추상화가 불필요
6. `analyze_main.py`가 결과를 터미널에 그대로 출력. DB에 결과를 저장하는 기능은 없음(순수 조회/분석 도구)

## 컴포넌트 상세

### `analysis/pipeline.py`

```python
def list_analyzable_keywords() -> list[str]:
    """memes 컬렉션에서 is_embedded=True인 문서들의 distinct keyword 목록 반환 (정렬됨)."""

def fetch_keyword_chunks(keyword: str) -> list[str]:
    """Qdrant mimori_chunks에서 payload.keyword == keyword인 포인트를 전부 scroll,
    payload["text"]만 추출해 리스트로 반환. 없으면 빈 리스트."""

def build_prompt(keyword: str, chunks: list[str]) -> str:
    """prompt_template.md를 읽어 {keyword}, {content}를 채운 문자열 반환.
    {content}는 chunks를 "\n\n---\n\n"로 join한 텍스트."""

def analyze(prompt: str, model: str = ANALYSIS_MODEL) -> str:
    """ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
    호출 후 응답 텍스트(message.content) 반환."""
```

- `fetch_keyword_chunks`는 기존 `DB/drant_clitent.py`의 `client`를 재사용한다. Qdrant `scroll`은 페이지 단위이므로 `next_page_offset`을 따라 끝까지 반복해 전부 모은다.
- `list_analyzable_keywords`는 기존 `DB/mongo_client.py`의 `get_collection()`을 재사용, `collection.distinct("keyword", {"is_embedded": True})`로 구현한다.

### `analysis/prompt_template.md` (초안)

```
다음은 밈/신조어 "{keyword}"에 대해 여러 출처에서 수집한 자료입니다.

{content}

위 자료를 바탕으로 다음을 분석해서 답변하세요:
1. 이 밈은 언제, 어떤 상황에서 쓰이는가
2. 어떤 뉘앙스(긍정/부정/유머 등)를 가지는가
3. 원래 뜻은 무엇이었는가
4. 그 뜻이 시간에 따라 어떻게 변화했는가 (변화가 없다면 없다고 명시)
```

이 파일은 향후 분석 기준이 바뀔 때 코드 수정 없이 텍스트만 고치면 되도록 별도 파일로 둔다.

## 설정 추가

`config/config_cilent.py`에 추가:
```python
# LLM 분석 설정
ANALYSIS_MODEL = "qwen3:latest"
ANALYSIS_PROMPT_PATH = "analysis/prompt_template.md"
```

## 에러 처리

기존 스크립트들의 스타일(예외를 잡아 메시지 출력 후 종료, 재시도 로직 없음)을 그대로 따른다:

- 분석 가능한 키워드가 하나도 없음 → "분석할 수 있는 키워드가 없습니다" 출력 후 종료
- 사용자가 목록에 없는 번호/값 입력 → "잘못된 선택입니다" 출력 후 종료 (재입력 루프 없음 — 다시 실행하면 됨)
- 선택한 키워드가 목록엔 있었는데 Qdrant에서 청크가 0개 (드문 불일치) → "Qdrant에서 청크를 찾을 수 없습니다" 출력 후 종료
- Ollama 서버 미실행 등으로 호출 실패 → 예외 메시지 그대로 출력 후 종료 (Ollama 실행 자체는 이 스크립트 책임 밖)

## 테스트 / 검증 방식

이 프로젝트에는 자동화된 테스트가 없다(`main.py`, `preprocess_main.py`, `embed_main.py` 모두 동일). 구현 후 `python analyze_main.py`를 직접 실행해 키워드 목록 → 선택 → 분석 결과 출력까지 눈으로 확인하는 방식으로 검증한다.

## 스코프에서 제외한 것

- Qdrant에서 직접 키워드 목록을 스크롤/집계하는 방식 (MongoDB `is_embedded=True` 조회로 충분하고 더 빠름)
- 벡터 유사도 기반 top-k 검색 (정확한 키워드 메타데이터 필터링이 이 목적에 더 적합)
- 분석 결과를 MongoDB 등에 다시 저장하는 배치/파이프라인화 (현재는 대화형 1회성 조회 도구만 필요)
- 대용량 텍스트에 대한 사전 제한/요약 전략 (실제로 컨텍스트 초과가 발생하기 전까지는 만들지 않음)

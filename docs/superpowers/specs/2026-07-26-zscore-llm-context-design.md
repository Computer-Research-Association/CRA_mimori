# z-score(유행 상태) 정보를 LLM 프롬프트에 반영하는 기능 설계

## 목적

`trend/trend_service.py`의 `get_meme_trend(keyword)`는 네이버 데이터랩 검색량 기반으로 밈/신조어의 유행 상태(z-score, 핫함/유행 중/감소/소멸)를 판정하는 기능인데, 현재 `main.py`에 import만 되어있고 실제로는 어디서도 호출되지 않는 미사용 상태다. 이 기능을 실제 LLM 답변 경로(`analyze_main.py`, `rag_main.py`)와 실험용 노트북(`analysis/langchain_playground.ipynb`)에 연결해, LLM이 텍스트 근거뿐 아니라 "지금 이 밈이 실제로 얼마나 유행 중인지"도 참고해서 답변하도록 한다.

## 아키텍처

```
trend/trend_service.py               # 함수 추가: format_trend_context(keyword) -> str
analysis/pipeline.py                 # build_prompt()에서 format_trend_context() 호출
analysis/rag_pipeline.py             # build_rag_prompt()에서 format_trend_context() 호출
analysis/prompt_template.md          # {trend_info} 자리표시자 추가
analysis/rag_prompt_template.md      # {trend_info} 자리표시자 추가
analysis/langchain_playground.ipynb  # 셀 추가: z-score 조회 + INCLUDE_TREND 토글
```

`trend/zscore.py`, `trend/datalab_client.py`, `trend/trend_service.py`의 기존 함수(`get_meme_trend`, `get_zscore`, `robust_scale`, `classify_trend`)는 전혀 수정하지 않는다. 새 함수 `format_trend_context()` 하나만 `trend_service.py`에 추가하고, CLI 두 곳과 노트북이 이 함수 하나를 공유해서 재사용한다.

- CLI(`analyze_main.py`, `rag_main.py`)는 항상 자동으로 시도한다 — 포함 여부를 묻는 프롬프트를 추가하지 않는다.
- 노트북만 `INCLUDE_TREND` 변수로 켜고 끌 수 있다 (실험 목적).

## 컴포넌트 상세

### `trend/trend_service.py` 추가 함수

```python
def format_trend_context(keyword: str) -> str:
    """
    get_meme_trend()를 호출해 LLM 프롬프트에 넣을 유행 상태 텍스트를 만든다.
    데이터랩 API 키 미설정/네트워크 오류/검색량 데이터 없음 등 어떤 이유로든
    조회할 수 없으면 빈 문자열을 반환한다 — 프롬프트에서 해당 부분이 그냥 사라지고,
    RAG 핵심 기능(텍스트 근거 기반 답변)은 막지 않는다.
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

`result["ratios"]`가 빈 리스트인 경우(데이터랩에 해당 키워드 검색량 데이터가 아예 없는 경우) `get_zscore([])`는 `(0.0, "유행 중")`을 반환하는데, 이 라벨을 그대로 프롬프트에 넣으면 "데이터가 없다"가 아니라 "유행 중"이라고 LLM에게 잘못 알려주게 된다. 그래서 `ratios`가 비어있으면 아예 빈 문자열을 반환해 이 라벨을 프롬프트에 노출하지 않는다. `trend/zscore.py` 자체의 이 동작(데이터 없음 → 0.0 → "유행 중")은 이번 스코프에서 수정하지 않는다.

### `analysis/pipeline.py` 변경

```python
from trend.trend_service import format_trend_context

def build_prompt(keyword: str, chunks: list[str]) -> str:
    with open(ANALYSIS_PROMPT_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    content = _CHUNK_SEPARATOR.join(chunks)
    trend_info = format_trend_context(keyword)
    return template.format(keyword=keyword, content=content, trend_info=trend_info)
```

### `analysis/rag_pipeline.py` 변경

```python
from trend.trend_service import format_trend_context

def build_rag_prompt(keyword: str, question: str, points: list[models.ScoredPoint]) -> str:
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

### `analysis/prompt_template.md` (전체, 변경 후)

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

### `analysis/rag_prompt_template.md` (전체, 변경 후)

```
다음은 밈/신조어 "{keyword}"에 대해 수집된 자료 중, 아래 질문과 관련성이 높은 것들입니다.

{trend_info}
{context}

위 자료를 근거로 다음 질문에 답변하세요. 자료에 없는 내용은 추측하지 말고 모른다고 답하세요.

질문: {question}
```

`trend_info`가 빈 문자열이면 그 줄이 그냥 비어서 사라지는 형태이며(약간의 빈 줄이 남을 수 있음, 기능에는 영향 없음), 이건 허용 가능한 수준의 사소한 코스메틱 이슈로 간주하고 별도 처리하지 않는다.

### `analysis/langchain_playground.ipynb` 변경

"2. 키워드 선택" 섹션과 "3. 질문 입력 + 임베딩" 섹션 사이에 새 섹션을 끼워 넣는다 (전체 섹션 수 7 → 8, 셀 수 15 → 17).

```
[Markdown] 2.5(신규). z-score / 유행 상태 확인
  설명: 선택한 키워드의 최근 검색량 기반 유행 상태를 네이버 데이터랩에서 조회합니다.
  실험하려면: INCLUDE_TREND를 True/False로 바꿔서 이 정보를 LLM에게 줄지 말지 결정하세요.
  (바꾼 뒤에는 이 셀부터 다시 실행하고 이어서 프롬프트 작성 셀을 실행해야 반영됩니다 — `INCLUDE_TREND`는 이 셀에서 정의되므로, 값만 바꾸고 이 셀을 재실행하지 않으면 이전 값이 그대로 남습니다.)
[Code]
  from trend.trend_service import format_trend_context

  trend_info = format_trend_context(KEYWORD)
  if trend_info:
      print(trend_info)
  else:
      print("z-score/유행 상태를 가져오지 못했습니다 (NAVER API 키 미설정이거나 데이터 없음).")

  INCLUDE_TREND = True
```

기존 "6. 프롬프트 작성" 섹션(신규 섹션 삽입으로 이제 "7. 프롬프트 작성")의 코드 셀을 아래로 교체한다:

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

해당 마크다운 설명도 "`{keyword}` / `{trend_info}` / `{context}` / `{question}` 네 개 자리표시자는 반드시 유지해야 함"으로 갱신한다.

## 에러 처리

`format_trend_context()`가 모든 예외를 내부에서 잡아 빈 문자열을 반환하므로, 호출부(`build_prompt`, `build_rag_prompt`, 노트북 셀)는 별도의 try/except 없이 그대로 사용한다. 데이터랩 API 키 미설정, 네트워크 오류, 키워드에 대한 검색량 데이터 없음 등 어떤 사유로든 z-score를 가져오지 못해도 기존 RAG/분석 흐름은 그대로 진행된다.

## 테스트 / 검증 방식

이 프로젝트에는 자동화된 테스트가 없다. 구현 후 다음을 실제로 실행해 확인한다:
- `python analyze_main.py`로 키워드 하나를 선택했을 때, 프롬프트에 유행 상태 정보가 포함되는지(또는 데이터랩 키가 없으면 자연스럽게 빠지는지) 확인
- `python rag_main.py`도 동일하게 확인
- `analysis/langchain_playground.ipynb`에서 `INCLUDE_TREND = True`/`False`를 각각 재실행해 실제 프롬프트(6번 셀 출력)에 유행 상태 텍스트가 포함/제외되는 것을 확인

## 스코프에서 제외한 것

- `trend/zscore.py`, `trend/datalab_client.py`, `trend/trend_service.py`의 기존 함수 로직 변경 — 그대로 재사용
- z-score 결과 캐싱/DB 저장 — 매번 실시간 조회
- `related_keywords` 커스터마이징 UI — `get_meme_trend()`의 기존 기본값 그대로 사용
- CLI에서 포함 여부를 묻는 인터랙티브 프롬프트 — 항상 자동 시도
- `trend/zscore.py`의 "데이터 없음 → 유행 중" 라벨링 자체를 고치는 것 — `format_trend_context()`에서 빈 `ratios`를 걸러내는 것으로 우회

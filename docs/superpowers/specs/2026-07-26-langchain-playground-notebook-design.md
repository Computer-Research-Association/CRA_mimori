# LangChain 실험용 노트북 설계 (`analysis/langchain_playground.ipynb`)

## 목적

기존 `rag_main.py`는 키워드 선택 → 질문 입력 → 하이브리드 검색 → LLM 답변까지 CLI로 한 번에 실행되는 단발성 도구다. 이 프로젝트의 핵심은 "입력 데이터(질문, 검색 파라미터, 프롬프트, 모델 설정)에 따라 LLM의 답변이 어떻게 달라지는지"를 실험하고 관찰하는 것인데, CLI는 이 반복 실험에 맞지 않는다.

이번에 추가하는 `analysis/langchain_playground.ipynb`는 같은 파이프라인(키워드 선택 → 임베딩 → 검색 → 프롬프트 조립 → LLM 호출)을 셀 단위로 쪼개, 각 셀을 독립적으로 재실행하며 실험할 수 있게 한다. 특히:

- 질문/검색 파라미터/프롬프트/모델 설정을 자유롭게 바꿔가며 답변 변화를 비교
- 벡터 검색이 실제로 어떤 청크를 가져오는지 dense-only / sparse-only / RRF 융합 세 가지 방식을 나란히 비교해서 확인

## 부수 작업: 유출된 API 키 제거

`analysis/qwen_nim.ipynb`에 NVIDIA API 키가 하드코딩된 채 git 히스토리에 커밋되어 있다(63b17bb). 이 노트북이 하던 역할(LangChain+NVIDIA 실험)을 새 노트북이 완전히 대체하므로 파일을 삭제한다.

- 파일 삭제만으로는 git 히스토리에 남은 키가 무효화되지 않으므로, **개발자가 NVIDIA 콘솔에서 해당 키를 직접 revoke/재발급해야 한다** (이 작업은 사람이 수행 — 자동화 스코프 밖).

## 아키텍처

```
analysis/qwen_nim.ipynb              # 삭제
analysis/langchain_playground.ipynb  # 신규 — 실험용 노트북
analysis/rag_pipeline.py             # 함수 추가: search_dense_only, search_sparse_only
```

기존 `rag_main.py`, `analysis/pipeline.py`, `analysis/rag_pipeline.py`의 `search_relevant_chunks`/`build_rag_prompt`, `embedding/encoder.py`, `config/config_cilent.py`는 그대로 재사용하고 수정하지 않는다(`rag_pipeline.py`에 함수 2개 추가하는 것 제외). `rag_main.py` CLI는 그대로 유지되며, 이 노트북이 대체하는 것이 아니라 실험용 도구를 별도로 추가하는 것이다.

새 검색 로직(dense-only, sparse-only)은 노트북에 직접 작성하지 않고 `analysis/rag_pipeline.py`에 함수로 추가한다 — 기존 컨벤션("실제 로직은 `analysis/` 모듈에 두고 진입점은 얇게 호출만 한다")을 노트북에도 동일하게 적용한다.

## 데이터 흐름 / 노트북 셀 구조

각 코드 셀 앞에는 "이 셀이 하는 일"과 "실험하려면 무엇을 바꾸면 되는지"를 설명하는 마크다운 셀을 둔다.

```
[Markdown] 1. 환경 설정
  설명: OS 환경변수(KMP_DUPLICATE_LIB_OK), 인코딩, import 준비. 한 번만 실행.
  바꿀 것 없음.
[Code] Cell 1
  - rag_main.py 상단과 동일한 환경설정 + sys.path + import
    (analysis.pipeline.list_analyzable_keywords,
     analysis.rag_pipeline.{search_relevant_chunks, search_dense_only,
       search_sparse_only, build_rag_prompt},
     embedding.encoder.{encode_batch, unload_model},
     config.config_cilent.NIM_KEY,
     langchain_nvidia_ai_endpoints.ChatNVIDIA)

[Markdown] 2. 키워드 선택
  설명: 임베딩된 밈 키워드 목록을 MongoDB에서 불러와 보여줌.
  실험하려면: 아래 KEYWORD 변수를 목록의 값 중 하나로 바꿔 재실행.
[Code] Cell 2
  keywords = list_analyzable_keywords()
  print(keywords)
  KEYWORD = "<목록에서 선택>"

[Markdown] 3. 질문 입력 + 임베딩
  설명: 자유 텍스트 질문을 BGE-M3로 dense/sparse 벡터로 변환.
  실험하려면: QUESTION만 바꾸면 됨 — 질문을 바꾸면 검색되는 청크가 달라지므로
  이 셀부터 다시 실행.
[Code] Cell 3
  QUESTION = "<질문 텍스트>"
  dense_vecs, lexical_weights = encode_batch([QUESTION])
  dense_vec, sparse = dense_vecs[0], lexical_weights[0]

[Markdown] 4. 검색 파라미터 + 실행
  설명: dense만 / sparse만 / RRF 융합 세 가지 방식으로 Qdrant에서 관련 청크 검색.
  실험하려면: TOP_K를 늘리거나 줄여서 더 많은/적은 근거 청크를 가져와볼 수 있음.
[Code] Cell 4
  TOP_K = 5
  dense_points  = search_dense_only(KEYWORD, dense_vec, TOP_K)
  sparse_points = search_sparse_only(KEYWORD, sparse, TOP_K)
  fused_points  = search_relevant_chunks(KEYWORD, dense_vec, sparse, TOP_K)

[Markdown] 5. 검색 결과 확인
  설명: 세 검색 결과를 나란히 비교 출력(점수, 제목, 출처, 본문 일부) — 어떤 데이터를
  실제로 가져왔는지 여기서 확인.
  실험하려면: 이 셀 자체는 결과 출력 전용이라 수정할 것 없음 — 위 셀들을 바꾸고
  재실행해서 차이를 비교.
[Code] Cell 5
  for label, points in [("DENSE", dense_points), ("SPARSE", sparse_points),
                        ("FUSED(RRF)", fused_points)]:
      print(f"--- {label} ---")
      for p in points:
          title = p.payload.get("title") or "제목 없음"
          url = p.payload.get("url") or "출처 없음"
          text = p.payload.get("text", "")
          print(f"  score={p.score:.4f} | {title} ({url})\n  {text[:150]}")

[Markdown] 6. 프롬프트 작성
  설명: LLM에게 실제로 전달할 프롬프트를 완성.
  실험하려면: PROMPT_TEMPLATE 문자열을 자유롭게 수정(지시문 추가, 어조 변경 등).
  {keyword}/{context}/{question} 자리표시자는 유지해야 함.
[Code] Cell 6
  PROMPT_TEMPLATE = """
  ... 자유롭게 수정 ...
  키워드: {keyword}
  자료:
  {context}
  질문: {question}
  """
  context = "\n\n---\n\n".join(
      f"[출처: {p.payload.get('title') or '제목 없음'} / {p.payload.get('url') or '출처 없음'}]\n{p.payload.get('text', '')}"
      for p in fused_points
  )
  prompt = PROMPT_TEMPLATE.format(keyword=KEYWORD, context=context, question=QUESTION)
  print(prompt)

[Markdown] 7. LLM 호출
  설명: 완성된 프롬프트를 NVIDIA LLM에 보내고 답변을 받음.
  실험하려면: MODEL/TEMPERATURE/TOP_P를 바꿔서 같은 프롬프트에도 답변이 어떻게
  달라지는지 비교.
[Code] Cell 7
  MODEL = "deepseek-ai/deepseek-v4-flash"
  TEMPERATURE = 1
  TOP_P = 0.95
  llm_client = ChatNVIDIA(model=MODEL, api_key=NIM_KEY, temperature=TEMPERATURE,
                          top_p=TOP_P, max_completion_tokens=16384, timeout=6000)
  response = llm_client.invoke([{"role": "user", "content": prompt}])
  print(response.content)
```

Cell 3~7을 반복 재실행하면서 질문/검색 파라미터/프롬프트/모델 설정을 바꿔가며 답변 변화를 바로 비교할 수 있다.

## 컴포넌트 상세: `analysis/rag_pipeline.py` 추가 함수

```python
def search_dense_only(
    keyword: str,
    dense_vec: list[float],
    top_k: int = RAG_TOP_K,
) -> list[models.ScoredPoint]:
    """dense 벡터만으로 검색(sparse/융합 없이). 순수 의미 유사도만 볼 때 사용."""

def search_sparse_only(
    keyword: str,
    sparse: dict[str, float],
    top_k: int = RAG_TOP_K,
) -> list[models.ScoredPoint]:
    """sparse(lexical) 벡터만으로 검색. 단어 일치 기반 유사도만 볼 때 사용."""
```

- 둘 다 `keyword`로 payload 필터링한 뒤, `client.query_points()`에 `query`/`using`만 지정(융합용 `prefetch`/`FusionQuery` 없이 단일 벡터 검색)해 상위 `top_k`개를 반환한다.
- `_to_sparse_vector`는 기존과 동일하게 `embedding.pipeline`에서 import해 재사용한다(로직 중복 없음).
- 기존 `search_relevant_chunks`(RRF 융합)는 변경하지 않는다.

## 에러 처리

노트북은 대화형 실행 환경이므로 기존 CLI 스크립트 스타일(예외를 잡아 메시지 출력 후 종료)을 그대로 따르지 않는다. 예외가 발생하면 해당 셀에서 자연스럽게 traceback이 뜨도록 두고, 값을 고쳐 그 셀만 재실행하면 되는 노트북의 장점을 그대로 살린다. 별도의 try/except 래핑을 추가하지 않는다.

## 테스트 / 검증 방식

이 프로젝트에는 자동화된 테스트가 없다(기존 스크립트들과 동일). 구현 후 노트북을 처음부터 끝까지 순서대로 실행해, 키워드 선택 → 임베딩 → 3종 검색 결과 비교 출력 → 프롬프트 미리보기 → LLM 답변까지 각 셀이 의도대로 동작하는지 눈으로 확인한다.

## 스코프에서 제외한 것

- `rag_main.py` CLI 대체 — 그대로 유지, 노트북은 실험용 도구를 별도로 추가하는 것
- 자동화된 테스트
- 답변/검색 결과를 MongoDB 등에 저장하는 기능 — 순수 실험/조회 도구
- LLM provider 추상화 계층 신설 — `ChatNVIDIA`를 노트북 셀에서 직접 생성
- 다회차 대화(멀티턴) 히스토리 관리 — 매번 단발 질의
- git 히스토리에서 유출된 API 키 제거(BFG 등) — 파일 삭제만 수행하고, 키 자체의 무효화(revoke)는 개발자가 NVIDIA 콘솔에서 직접 수행

# 밈 데이터 대시보드 (정적 웹사이트) 설계

## 배경 / 목적

지금까지 수집(크롤링)·전처리·임베딩·LLM 분석까지 진행된 밈 데이터를, 코드나 터미널이 아니라 브라우저에서 한눈에 볼 수 있는 정적 웹페이지로 만든다. 사용자는 웹 개발 경험이 없으므로, 로컬에서 먼저 미리보기로 확인하고 마음에 들면 온라인(GitHub Pages 등)에 그대로 배포할 수 있는 구조를 목표로 한다.

## 목표

- 수집된 키워드 목록을 볼 수 있다.
- 키워드별 수집량 통계(전체 개수, 소스별 분포)를 볼 수 있다.
- 이미 LLM 분석이 끝난 키워드는 그 분석 결과(전문)를 볼 수 있다.
- 별도 백엔드/DB 연결 없이, 정적 파일만으로 로컬 미리보기와 온라인 배포가 모두 가능하다.

## 비목표 (지금은 안 함)

- 브라우저에서 버튼을 눌러 실시간으로 새 분석을 돌리는 기능 (브라우저는 로컬 Mongo/Qdrant/Ollama를 호출할 수 없음 — 별도 로컬 서버가 필요한 완전히 다른 구조이며, 이번 범위 밖)
- 원본 게시글/댓글 원문 전체 노출
- 자동 갱신(파일시스템 watch, 크론 등) — 필요할 때 수동으로 다시 실행하는 것으로 충분
- 여러 파일로 쪼갠 데이터(JSON per 키워드) — 지금 키워드 규모(개수 자리)에서는 단일 JSON으로 충분 (YAGNI)

## 아키텍처

```
dashboard_main.py          ← 루트 진입점, `python dashboard_main.py`로 실행
dashboard/
  __init__.py
  export.py                 ← Mongo 통계 수집 + 기존 analysis.pipeline 재사용해 LLM 분석 → data.json 저장
  public/
    index.html
    style.css
    script.js
    data.json                ← export.py가 생성하는 스냅샷 (매번 덮어씀)
```

- `dashboard_main.py`는 기존 `main.py` / `preprocess_main.py` / `embed_main.py` / `analyze_main.py`와 동일한 "루트 `*_main.py` 진입점 + 전용 패키지" 패턴을 따른다.
- 패키지 이름을 `site`가 아니라 `dashboard`로 정한 이유: Python이 인터프리터 시작 시 자동으로 임포트하는 표준 라이브러리 모듈 이름이 `site`라서, 프로젝트 루트에 `site/` 패키지를 두면 그 표준 모듈을 가려서 문제가 생길 수 있음.
- `dashboard/export.py`는 새로운 DB 접근 로직을 만들지 않고, 기존 `DB/mongo_client.py::get_collection()`과 `analysis/pipeline.py`의 `fetch_keyword_chunks`, `build_prompt`, `analyze` 함수를 그대로 재사용한다.

## 데이터 흐름

1. 사용자가 `python dashboard_main.py` 실행.
2. `export.py`가 `memes` 컬렉션 전체를 훑어 키워드별로 그룹핑 — 각 키워드마다 `총 문서 수`, `source별 개수`, `is_embedded 여부` 집계.
3. `is_embedded=True`인 키워드에 대해서만 `analysis.pipeline`의 함수들을 호출해 청크 조회 → 프롬프트 조립 → Ollama 호출 → 분석 텍스트 획득.
4. 위 결과를 하나의 JSON 구조로 합쳐 `dashboard/public/data.json`에 저장(UTF-8, 한글 그대로 — `ensure_ascii=False`).
5. 완료 메시지와 함께 로컬 미리보기 방법 안내 출력 (`dashboard/public` 폴더에서 `python -m http.server` 실행 후 브라우저로 열기).
6. `index.html`을 브라우저로 열면 `script.js`가 `fetch("data.json")`으로 읽어와 렌더링. 왼쪽에 키워드 목록(개수 뱃지 포함), 클릭하면 오른쪽에 소스별 분포 + 분석 전문(없으면 "분석 없음") 표시.

### data.json 스키마

```json
{
  "generated_at": "2026-07-15T12:34:56+09:00",
  "summary": {
    "total_keywords": 8,
    "total_docs": 512,
    "analyzed_keywords": 6
  },
  "keywords": [
    {
      "keyword": "에스파",
      "total_count": 67,
      "by_source": { "dcinside": 40, "youtube": 27 },
      "is_embedded": true,
      "analysis": "### 1. 이 밈은...(LLM 분석 전문 마크다운 텍스트)"
    },
    {
      "keyword": "새키워드",
      "total_count": 3,
      "by_source": { "dcinside": 3 },
      "is_embedded": false,
      "analysis": null
    }
  ]
}
```

`keywords` 배열은 `keyword` 기준 오름차순 정렬.

## 에러 처리

- 특정 키워드의 LLM 분석 중 예외(Ollama 응답 실패 등)가 발생해도 스크립트 전체를 멈추지 않는다 — 그 키워드는 `analysis: null`로 남기고 다음 키워드로 진행. 콘솔에 어떤 키워드가 실패했는지 출력.
- Mongo/Qdrant 연결 자체가 안 되는 경우(예: `.env` 미설정)는 기존 다른 `*_main.py`들과 동일하게 예외가 그대로 터지도록 둔다 (이 프로젝트의 기존 컨벤션 — 별도 방어 로직 없음).

## 검증 방법

이 프로젝트에는 자동화된 테스트 프레임워크가 없다 (기존 컨벤션). 다음을 수동으로 확인한다.

1. `python dashboard_main.py` 실행 → 에러 없이 완료되고 `dashboard/public/data.json`이 생성되는지 확인.
2. `data.json`을 열어 실제 Mongo 데이터와 개수가 맞는지, 분석된 키워드는 `analysis` 필드가 채워져 있는지 확인.
3. `dashboard/public`에서 `python -m http.server` 실행 후 `http://localhost:8000`을 브라우저로 열어, 키워드 목록/통계/분석 전문이 화면에 정상적으로 나오는지 눈으로 확인.

## 비주얼 스타일

구현 단계에서 `frontend-design` 스킬을 사용해 색감/타이포그래피 등 톤을 잡는다. 이 설계 문서는 로직/데이터 구조만 다루고, 구체적인 비주얼 디테일은 구현 단계에서 결정한다.

## 배포 (참고, 지금 범위 아님)

정적 파일(`dashboard/public/` 내용)만 있으면 GitHub Pages든 Netlify든 그대로 올릴 수 있다. `data.json`이 스냅샷이라, 데이터가 갱신될 때마다 `dashboard_main.py`를 다시 실행하고 재배포해야 최신 상태가 반영된다. 이번 이터레이션에서는 로컬 미리보기까지만 다루고, 실제 배포는 사용자가 원할 때 별도로 진행한다.

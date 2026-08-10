# 프론트엔드 웹사이트 설계 (React + nginx)

## 목적

지금까지 만든 백엔드 API(`/api/analyze`, `/api/rag`, `/api/trend/<keyword>`, `/api/keywords`, `/api/crawl-request`)를 실제로 브라우저에서 쓸 수 있는 웹사이트로 노출한다. 단일 페이지: 키워드 선택 → 뜻/유래/트렌드 자동 표시 → 자유 질문(RAG) → (없는 키워드면) 수집 요청.

## 범위

**포함**: React+Vite 단일 페이지 앱, nginx를 통한 정적 파일 서빙 + `/api` 리버스프록시, 새 키워드 수집 요청 UI(폴링 포함), RAG 소스 필터 체크박스.

**제외**: 로그인/회원가입, 여러 페이지 라우팅, 검색 기록 저장 등 — 전부 YAGNI로 제외. 데이터 소스(크롤러) 자체를 켜고 끄는 관리자 기능도 이 스펙엔 없음(완전히 별개, 인증 필요, 요청 안 됨).

## 아키텍처

```
사용자 브라우저 (:80)
  → nginx (신규 컨테이너, docker-compose "web" 서비스)
     ├─ /            → React 빌드 정적 파일
     └─ /api/*        → api 컨테이너(:5000)로 리버스프록시
```

프론트/백엔드가 nginx 뒤 같은 origin이라 CORS 문제가 없고, fetch는 전부 상대경로(`/api/...`)로 호출한다. `frontend/Dockerfile`은 멀티스테이지 빌드(`node:20-slim`으로 `npm run build` → `nginx:alpine`이 결과물만 서빙)라 EC2에 Node.js를 따로 설치할 필요가 없다.

**방화벽**: EC2 보안그룹에 80번 포트 추가 필요(5000번과 별개로 관리자에게 한 번 더 요청).

## 페이지 컴포넌트 & 상태 (React useState만 사용, 상태관리 라이브러리 불필요)

```
App
├─ KeywordSelector      : /api/keywords 목록 + 드롭다운. 목록에 없는 값 입력 시 "수집 요청" 버튼 노출
├─ CrawlRequestPanel     : POST /api/crawl-request → 이후 GET /api/crawl-request/<kw>를 5초 간격 폴링
│                          status가 "done"이면 KeywordSelector 목록 갱신 + 자동으로 그 키워드 선택
├─ AnalysisPanel         : 키워드 선택 시 GET /api/trend/<kw> + POST /api/analyze 동시 호출, 로딩 스피너
└─ RagPanel
   ├─ SourceFilter       : 체크박스 6개(tavily/youtube/namuwiki/natepann/dcinside/todayhumor), 기본 전체 선택
   └─ 질문 입력 → POST /api/rag {keyword, question, sources: 체크된 것만} → 답변 + 출처 링크
```

**소스 이름 목록은 프론트에 하드코딩**한다(`main.py`의 `CRAWLERS` 딕셔너리 키와 동일한 6개, 거의 안 바뀌는 값이라 별도 API로 안 뺌 — YAGNI).

## 데이터 흐름 상세

### 키워드 선택 (기존 키워드)
1. `GET /api/trend/<keyword>`, `POST /api/analyze {"keyword"}` 동시 호출(로딩 스피너, 수 초~수십 초 소요 — 반드시 "분석 중..." 표시)
2. 결과 표시 후 질문 입력창 활성화

### 새 키워드 (목록에 없음)
1. 검색창에 목록에 없는 값 입력 → "'X'는 아직 데이터가 없습니다. 수집을 요청하시겠습니까? (보통 수십 분 소요)" + [네, 요청합니다] 버튼
2. 클릭 → `POST /api/crawl-request {"keyword": "X"}` → 202 응답 받으면 "수집 중..." 화면으로 전환
3. 5초마다 `GET /api/crawl-request/X` 폴링 → `status`가:
   - `queued`/`running`: 계속 "수집 중..." 표시
   - `done`: 자동으로 키워드 목록 갱신 + 그 키워드로 분석 화면 전환
   - `failed`: `error` 메시지 표시 + 재시도 버튼(다시 `POST /api/crawl-request`, 백엔드가 failed 상태를 큐로 리셋)

### RAG 질문 (소스 필터 포함)
1. 체크박스로 원하는 소스만 선택(기본 전체 선택 상태)
2. 질문 입력 후 전송 → `POST /api/rag {"keyword", "question", "sources": [체크된 것들]}`
3. 답변 + 출처(제목/링크) 표시

## 백엔드에 필요한 작은 변경 (이 스펙에 포함, `api/routes.py` 수정)

`rag_endpoint()`가 요청 body의 선택적 `sources` 필드를 읽어서, 이미 `sources` 파라미터를 지원하는 기존 `search_relevant_chunks()`에 그대로 전달한다:

```python
sources = data.get("sources")  # list[str] | None, 생략하면 전체 소스 검색(기존 동작과 동일)
...
points = search_relevant_chunks(keyword, dense_vecs[0], lexical_weights[0], sources=sources, is_relevant=True)
```

값을 안 보내면(`None`) 기존 동작과 완전히 동일 — 하위호환.

## 에러 처리

백엔드가 이미 `{"error": "메시지"}`로 통일되어 있으므로, 프론트는 그 문자열을 그대로 화면에 표시. 네트워크 자체 실패(서버 다운 등)는 "서버에 연결할 수 없습니다"로 별도 처리.

## 테스트

- React 컴포넌트: API 호출을 mock해서 로딩/성공/에러/폴링 상태 전환 단위 테스트
- `sources` 파라미터 관련 `/api/rag` 백엔드 테스트: 값 없이 호출 시 기존과 동일 동작(회귀), 특정 소스 리스트 전달 시 `search_relevant_chunks`에 그대로 전달되는지
- 수동: 로컬에서 `docker compose up`으로 nginx+api+mongo+qdrant 띄우고 브라우저로 전체 플로우 확인

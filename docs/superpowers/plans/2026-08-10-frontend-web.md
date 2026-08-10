# 프론트엔드 웹사이트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 백엔드 API 위에 React 단일 페이지 웹사이트를 만들어 EC2에서 nginx로 서빙하고, 남들이 브라우저로 밈을 검색/질문할 수 있게 한다.

**Architecture:** `frontend/`에 Vite+React 프로젝트. nginx가 빌드된 정적 파일을 서빙하고 `/api/*`는 기존 `api` 컨테이너로 리버스프록시(같은 origin이라 CORS 불필요). App.jsx가 최상위 상태(선택된 키워드 등)를 들고, 하위 컴포넌트는 props/콜백으로만 통신 — 별도 상태관리 라이브러리 없음.

**Tech Stack:** React 18 + Vite 5, Vitest + React Testing Library(테스트), 순수 CSS, nginx(정적 서빙+리버스프록시), 기존 백엔드 API 그대로 재사용.

## Global Constraints

- 참고 스펙: `docs/superpowers/specs/2026-08-10-frontend-web-design.md`, `docs/superpowers/specs/2026-08-10-on-demand-crawl-design.md`
- 프론트/백엔드 fetch는 전부 **상대경로**(`/api/...`) — nginx 리버스프록시로 같은 origin이 되므로 CORS 설정 불필요
- 상태관리 라이브러리(Redux 등) 금지 — React `useState`/`useEffect`만 사용(YAGNI)
- TypeScript 미사용, 순수 JSX — 새 개념 최소화
- 소스 목록(체크박스용)은 프론트에 하드코딩: `["tavily", "youtube", "namuwiki", "natepann", "dcinside", "todayhumor"]`
- 크롤 요청 상태 폴링 주기: 5초
- gunicorn `--timeout 600`(기존 설정)과 nginx의 `proxy_read_timeout`을 반드시 맞춘다 — 안 맞추면 nginx가 gunicorn보다 먼저 타임아웃시켜서 느린 analyze/rag 응답이 끊김
- Python 쪽 테스트 컨벤션(Task 1에만 해당): pytest 없이 `def test_*()` + `if __name__ == "__main__":` + `print("ALL PASS ✅")`
- JS 쪽 테스트: Vitest, `npm test`로 실행

---

## File Structure

```
api/routes.py                        # 수정 — /api/rag가 선택적 sources 파라미터 지원

frontend/
  package.json
  vite.config.js
  index.html
  Dockerfile                         # 멀티스테이지(node 빌드 -> nginx 서빙)
  nginx.conf
  src/
    main.jsx
    App.jsx
    setupTests.js
    api.js                           # 백엔드 호출 래퍼 + AVAILABLE_SOURCES 상수
    api.test.js
    components/
      KeywordSelector.jsx
      KeywordSelector.test.jsx
      CrawlRequestPanel.jsx
      CrawlRequestPanel.test.jsx
      AnalysisPanel.jsx
      AnalysisPanel.test.jsx
      SourceFilter.jsx
      RagPanel.jsx
      RagPanel.test.jsx

docker-compose.yml                   # 수정 — web(nginx) 서비스 추가
```

---

### Task 1: 백엔드 — `/api/rag` 소스 필터 파라미터

**Files:**
- Modify: `api/routes.py:rag_endpoint`
- Test: `tests/test_api_rag_endpoint.py` (기존 파일에 케이스 추가)

**Interfaces:**
- Consumes: `analysis.rag_pipeline.search_relevant_chunks(..., sources: list[str] | None = None, ...)`(이미 존재)
- Produces: `POST /api/rag` body에 선택적 `"sources": list[str]` 필드 지원. 생략 시 기존과 동일(전체 소스 검색)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_api_rag_endpoint.py` 파일의 `if __name__ == "__main__":` 블록 위에 아래 테스트 함수 추가:

```python
def test_sources_파라미터가_search_relevant_chunks에_그대로_전달된다():
    fake_point = SimpleNamespace(
        payload={"title": "야르 뜻 정리", "url": "https://example.com/야르", "text": "본문"}
    )
    calls = {}

    original_encode = _patch(routes, "encode_batch", lambda texts: ([[0.1, 0.2]], [{}]))

    def fake_search(keyword, dense_vec, sparse, **kwargs):
        calls["search_kwargs"] = kwargs
        return [fake_point]

    original_search = _patch(routes, "search_relevant_chunks", fake_search)
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: None)
    original_ctx = _patch(routes, "format_trend_context", lambda keyword, result=None: "")
    original_build = _patch(routes, "build_rag_prompt", lambda keyword, question, points, trend_info=None: "프롬프트")
    original_analyze = _patch(routes, "analyze", lambda prompt: "답변")
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post(
            "/api/rag",
            json={"keyword": "야르", "question": "무슨 뜻이야?", "sources": ["tavily", "youtube"]},
        )
        assert resp.status_code == 200, resp.status_code
        assert calls["search_kwargs"]["sources"] == ["tavily", "youtube"], calls
        assert calls["search_kwargs"]["is_relevant"] is True, calls
    finally:
        routes.encode_batch = original_encode
        routes.search_relevant_chunks = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_rag_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] sources 파라미터가 search_relevant_chunks로 전달됨")


def test_sources_생략하면_None으로_전달된다_기존동작():
    fake_point = SimpleNamespace(
        payload={"title": "야르 뜻 정리", "url": "https://example.com/야르", "text": "본문"}
    )
    calls = {}

    original_encode = _patch(routes, "encode_batch", lambda texts: ([[0.1, 0.2]], [{}]))

    def fake_search(keyword, dense_vec, sparse, **kwargs):
        calls["search_kwargs"] = kwargs
        return [fake_point]

    original_search = _patch(routes, "search_relevant_chunks", fake_search)
    original_cached = _patch(routes, "get_cached_trend", lambda keyword: None)
    original_ctx = _patch(routes, "format_trend_context", lambda keyword, result=None: "")
    original_build = _patch(routes, "build_rag_prompt", lambda keyword, question, points, trend_info=None: "프롬프트")
    original_analyze = _patch(routes, "analyze", lambda prompt: "답변")
    try:
        app = create_app()
        client = app.test_client()
        resp = client.post("/api/rag", json={"keyword": "야르", "question": "무슨 뜻이야?"})
        assert resp.status_code == 200, resp.status_code
        assert calls["search_kwargs"]["sources"] is None, calls
    finally:
        routes.encode_batch = original_encode
        routes.search_relevant_chunks = original_search
        routes.get_cached_trend = original_cached
        routes.format_trend_context = original_ctx
        routes.build_rag_prompt = original_build
        routes.analyze = original_analyze
    print("[OK] sources 생략 시 None (기존 동작과 동일)")
```

같은 파일의 `if __name__ == "__main__":` 블록에 두 함수 호출 추가:

```python
    test_sources_파라미터가_search_relevant_chunks에_그대로_전달된다()
    test_sources_생략하면_None으로_전달된다_기존동작()
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_api_rag_endpoint.py`
Expected: `AssertionError` (현재 `search_relevant_chunks` 호출에 `sources` 키워드 인자 자체가 없어서 `calls["search_kwargs"]`에 `sources` 키가 없음)

- [ ] **Step 3: `api/routes.py`의 `rag_endpoint` 수정**

기존:
```python
    search_query = build_search_query(keyword, question)
    dense_vecs, lexical_weights = encode_batch([search_query])
    points = search_relevant_chunks(keyword, dense_vecs[0], lexical_weights[0], is_relevant=True)
```

변경 후:
```python
    sources = data.get("sources")  # list[str] | None. 생략하면 전체 소스 검색(기존 동작과 동일)

    search_query = build_search_query(keyword, question)
    dense_vecs, lexical_weights = encode_batch([search_query])
    points = search_relevant_chunks(
        keyword, dense_vecs[0], lexical_weights[0], sources=sources, is_relevant=True
    )
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `PYTHONIOENCODING=utf-8 uv run python tests/test_api_rag_endpoint.py`
Expected: `ALL PASS ✅`

- [ ] **Step 5: 커밋**

```bash
git add api/routes.py tests/test_api_rag_endpoint.py
git commit -m "feat(api): /api/rag에 선택적 sources 필터 파라미터 추가"
```

---

### Task 2: 프론트엔드 프로젝트 뼈대 + Docker/nginx 인프라

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.js`, `frontend/index.html`, `frontend/src/main.jsx`, `frontend/src/App.jsx`(임시 플레이스홀더), `frontend/src/setupTests.js`, `frontend/src/App.test.jsx`
- Create: `frontend/Dockerfile`, `frontend/nginx.conf`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `npm run build`가 `frontend/dist/`에 정적 파일 생성. `npm test`로 Vitest 실행 가능. `docker compose build web`로 nginx 이미지 빌드 가능.

- [ ] **Step 1: `frontend/package.json` 작성**

```json
{
  "name": "mimori-frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.8",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.2",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.1",
    "vite": "^5.4.1",
    "vitest": "^2.0.5"
  }
}
```

- [ ] **Step 2: `frontend/vite.config.js` 작성**

```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/setupTests.js',
    globals: true,
  },
})
```

- [ ] **Step 3: `frontend/index.html` 작성**

```html
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>mimori — 밈/신조어 검색</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 4: `frontend/src/main.jsx` 작성**

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 5: `frontend/src/setupTests.js` 작성**

```javascript
import '@testing-library/jest-dom'
```

- [ ] **Step 6: 임시 `frontend/src/App.jsx` 작성 (Task 8에서 실제 내용으로 교체됨)**

```jsx
export default function App() {
  return <h1>mimori 준비 중</h1>
}
```

- [ ] **Step 7: 실패하는 테스트 작성**

`frontend/src/App.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import App from './App.jsx'

describe('App', () => {
  it('렌더링된다', () => {
    render(<App />)
    expect(screen.getByText(/mimori/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 8: 의존성 설치 후 테스트 실행**

Run (frontend 디렉토리에서):
```bash
cd frontend
npm install
npm test
```
Expected: `App > 렌더링된다` 통과 (Step 6에서 이미 App.jsx를 만들어뒀으므로 이 단계는 RED가 아니라 툴체인 자체가 도는지 확인하는 용도)

- [ ] **Step 9: 프로덕션 빌드 확인**

Run: `npm run build` (frontend 디렉토리에서)
Expected: 에러 없이 종료, `frontend/dist/index.html` 생성됨

- [ ] **Step 10: `frontend/nginx.conf` 작성**

```nginx
server {
    listen 80;

    location / {
        root /usr/share/nginx/html;
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://api:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # gunicorn --timeout 600(docker-compose.yml의 api 서비스)과 맞춤 —
        # 안 맞추면 nginx가 느린 analyze/rag 응답을 gunicorn보다 먼저 끊어버림
        proxy_read_timeout 600s;
    }
}
```

- [ ] **Step 11: `frontend/Dockerfile` 작성 (멀티스테이지)**

```dockerfile
FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

- [ ] **Step 12: `docker-compose.yml`에 `web` 서비스 추가**

`qdrant` 서비스 블록 다음, `volumes:` 블록 앞에 추가:

```yaml
  web:
    build: ./frontend
    container_name: mimori-web
    ports:
      - "80:80"
    depends_on:
      - api
    restart: unless-stopped
```

- [ ] **Step 13: `.dockerignore`에 `frontend/node_modules` 무시 확인**

`.dockerignore` 파일을 열어 `node_modules`가 없으면 한 줄 추가 (Python용 `.dockerignore`가 이미 있다면 확인만, 없으면 생성):

```
node_modules
frontend/dist
```

- [ ] **Step 14: 커밋**

```bash
git add frontend/package.json frontend/vite.config.js frontend/index.html frontend/src/main.jsx frontend/src/App.jsx frontend/src/setupTests.js frontend/src/App.test.jsx frontend/nginx.conf frontend/Dockerfile docker-compose.yml .dockerignore
git commit -m "feat(frontend): Vite+React 프로젝트 뼈대 + nginx 배포 인프라"
```

(주의: `frontend/node_modules`, `frontend/dist`는 커밋 대상에서 제외 — `frontend/.gitignore`를 만들어서 `node_modules`, `dist`를 넣어둘 것. 위 `git add`가 이 두 폴더를 명시적으로 지정하지 않으므로 `.gitignore` 없이 `git status`로 먼저 확인 후 커밋.)

---

### Task 3: API 클라이언트 (`frontend/src/api.js`)

**Files:**
- Create: `frontend/src/api.js`
- Test: `frontend/src/api.test.js`

**Interfaces:**
- Produces:
  - `AVAILABLE_SOURCES: string[]` — `["tavily", "youtube", "namuwiki", "natepann", "dcinside", "todayhumor"]`
  - `fetchKeywords(): Promise<string[]>`
  - `fetchTrend(keyword: string): Promise<object | null>` — 캐시 없음(404)이면 `null`
  - `analyzeKeyword(keyword: string): Promise<{result: string, trend: object|null}>`
  - `askRag(keyword: string, question: string, sources: string[]|null): Promise<{answer: string, sources: object[], trend: object|null}>`
  - `requestCrawl(keyword: string): Promise<{keyword: string, status: string}>`
  - `fetchCrawlStatus(keyword: string): Promise<{keyword, status, requested_at, completed_at, error}>`
  - 전부 실패 시(`res.ok === false`) 백엔드의 `{"error": "..."}`를 메시지로 하는 `Error`를 던진다

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/api.test.js`:

```javascript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchKeywords, fetchTrend, analyzeKeyword, askRag, requestCrawl, fetchCrawlStatus, AVAILABLE_SOURCES } from './api.js'

beforeEach(() => {
  global.fetch = vi.fn()
})

function jsonResponse(body, ok = true, status = ok ? 200 : 400) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
  })
}

describe('AVAILABLE_SOURCES', () => {
  it('6개 소스를 포함한다', () => {
    expect(AVAILABLE_SOURCES).toEqual([
      'tavily', 'youtube', 'namuwiki', 'natepann', 'dcinside', 'todayhumor',
    ])
  })
})

describe('fetchKeywords', () => {
  it('/api/keywords를 호출하고 목록을 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keywords: ['야르', '쌰갈'] }))
    const result = await fetchKeywords()
    expect(fetch).toHaveBeenCalledWith('/api/keywords')
    expect(result).toEqual(['야르', '쌰갈'])
  })
})

describe('fetchTrend', () => {
  it('404면 null을 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({ error: '없음' }, false, 404))
    const result = await fetchTrend('없는키워드')
    expect(result).toBeNull()
  })

  it('있으면 트렌드 객체를 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({ status: '유행 중', final_z: 1.2 }))
    const result = await fetchTrend('야르')
    expect(fetch).toHaveBeenCalledWith('/api/trend/야르')
    expect(result.status).toBe('유행 중')
  })
})

describe('analyzeKeyword', () => {
  it('POST /api/analyze를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ result: '분석결과', trend: null }))
    const result = await analyzeKeyword('야르')
    expect(fetch).toHaveBeenCalledWith('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '야르' }),
    })
    expect(result.result).toBe('분석결과')
  })

  it('실패 응답이면 에러 메시지를 담은 Error를 던진다', async () => {
    fetch.mockReturnValue(jsonResponse({ error: '데이터를 찾을 수 없습니다' }, false, 404))
    await expect(analyzeKeyword('없는키워드')).rejects.toThrow('데이터를 찾을 수 없습니다')
  })
})

describe('askRag', () => {
  it('sources를 포함해서 POST /api/rag를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ answer: '답변', sources: [], trend: null }))
    await askRag('야르', '무슨 뜻이야?', ['tavily'])
    expect(fetch).toHaveBeenCalledWith('/api/rag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '야르', question: '무슨 뜻이야?', sources: ['tavily'] }),
    })
  })
})

describe('requestCrawl', () => {
  it('POST /api/crawl-request를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keyword: '흘로망', status: 'queued' }))
    const result = await requestCrawl('흘로망')
    expect(fetch).toHaveBeenCalledWith('/api/crawl-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '흘로망' }),
    })
    expect(result.status).toBe('queued')
  })
})

describe('fetchCrawlStatus', () => {
  it('GET /api/crawl-request/<keyword>를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keyword: '흘로망', status: 'running' }))
    const result = await fetchCrawlStatus('흘로망')
    expect(fetch).toHaveBeenCalledWith('/api/crawl-request/흘로망')
    expect(result.status).toBe('running')
  })
})
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `npm test` (frontend 디렉토리에서)
Expected: `Cannot find module './api.js'` 계열 실패

- [ ] **Step 3: `frontend/src/api.js` 작성**

```javascript
const BASE = '/api'

export const AVAILABLE_SOURCES = [
  'tavily', 'youtube', 'namuwiki', 'natepann', 'dcinside', 'todayhumor',
]

async function handleResponse(res) {
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.error || `요청 실패 (${res.status})`)
  }
  return data
}

export async function fetchKeywords() {
  const res = await fetch(`${BASE}/keywords`)
  const data = await handleResponse(res)
  return data.keywords
}

export async function fetchTrend(keyword) {
  const res = await fetch(`${BASE}/trend/${encodeURIComponent(keyword)}`)
  if (res.status === 404) return null
  return handleResponse(res)
}

export async function analyzeKeyword(keyword) {
  const res = await fetch(`${BASE}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword }),
  })
  return handleResponse(res)
}

export async function askRag(keyword, question, sources) {
  const res = await fetch(`${BASE}/rag`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword, question, sources }),
  })
  return handleResponse(res)
}

export async function requestCrawl(keyword) {
  const res = await fetch(`${BASE}/crawl-request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword }),
  })
  return handleResponse(res)
}

export async function fetchCrawlStatus(keyword) {
  const res = await fetch(`${BASE}/crawl-request/${encodeURIComponent(keyword)}`)
  return handleResponse(res)
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `npm test` (frontend 디렉토리에서)
Expected: 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/api.js frontend/src/api.test.js
git commit -m "feat(frontend): 백엔드 API 클라이언트 모듈"
```

---

### Task 4: `KeywordSelector` + `CrawlRequestPanel` 컴포넌트

**Files:**
- Create: `frontend/src/components/KeywordSelector.jsx`, `frontend/src/components/KeywordSelector.test.jsx`
- Create: `frontend/src/components/CrawlRequestPanel.jsx`, `frontend/src/components/CrawlRequestPanel.test.jsx`

**Interfaces:**
- Consumes: `api.js`의 `requestCrawl`, `fetchCrawlStatus`(Task 3)
- Produces:
  - `KeywordSelector({ keywords, onSelect, onNewKeyword })` — `keywords: string[]`, `onSelect(keyword: string)`, `onNewKeyword(keyword: string)`. 입력값이 `keywords`에 있으면 `onSelect` 호출, 없으면 `onNewKeyword` 호출.
  - `CrawlRequestPanel({ keyword, onDone })` — 마운트 시 `requestCrawl(keyword)` 1회 호출 후 5초 간격으로 `fetchCrawlStatus(keyword)` 폴링. `status === "done"`이면 `onDone(keyword)` 호출하고 폴링 중단. `status === "failed"`면 에러+재시도 버튼 표시(재시도 시 `requestCrawl` 다시 호출).

- [ ] **Step 1: 실패하는 테스트 작성 — `KeywordSelector`**

`frontend/src/components/KeywordSelector.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import KeywordSelector from './KeywordSelector.jsx'

describe('KeywordSelector', () => {
  it('목록에 있는 키워드를 입력하면 onSelect가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['야르', '쌰갈']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('textbox'), '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(onSelect).toHaveBeenCalledWith('야르')
    expect(onNewKeyword).not.toHaveBeenCalled()
  })

  it('목록에 없는 키워드를 입력하면 onNewKeyword가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['야르']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('textbox'), '흘로망')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(onNewKeyword).toHaveBeenCalledWith('흘로망')
    expect(onSelect).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `npm test` (frontend 디렉토리)
Expected: `Failed to resolve import "./KeywordSelector.jsx"`

- [ ] **Step 3: `frontend/src/components/KeywordSelector.jsx` 작성**

```jsx
import { useState } from 'react'

export default function KeywordSelector({ keywords, onSelect, onNewKeyword }) {
  const [value, setValue] = useState('')

  function handleSubmit(e) {
    e.preventDefault()
    const trimmed = value.trim()
    if (!trimmed) return
    if (keywords.includes(trimmed)) {
      onSelect(trimmed)
    } else {
      onNewKeyword(trimmed)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <input
        list="keyword-options"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="밈/신조어를 입력하세요"
      />
      <datalist id="keyword-options">
        {keywords.map((kw) => (
          <option key={kw} value={kw} />
        ))}
      </datalist>
      <button type="submit">검색</button>
    </form>
  )
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인 (`KeywordSelector`)**

Run: `npm test` (frontend 디렉토리)
Expected: `KeywordSelector`의 두 테스트 통과

- [ ] **Step 5: 실패하는 테스트 작성 — `CrawlRequestPanel`**

`frontend/src/components/CrawlRequestPanel.test.jsx`:

```jsx
import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import CrawlRequestPanel from './CrawlRequestPanel.jsx'
import * as api from '../api.js'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('CrawlRequestPanel', () => {
  it('마운트되면 requestCrawl을 1회 호출한다', async () => {
    const requestCrawlSpy = vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({ keyword: '흘로망', status: 'queued' })

    render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)

    await waitFor(() => expect(requestCrawlSpy).toHaveBeenCalledWith('흘로망'))
    expect(requestCrawlSpy).toHaveBeenCalledTimes(1)
  })

  it('status가 done이 되면 onDone을 호출하고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    const fetchStatusSpy = vi.spyOn(api, 'fetchCrawlStatus')
      .mockResolvedValueOnce({ keyword: '흘로망', status: 'running' })
      .mockResolvedValueOnce({ keyword: '흘로망', status: 'done' })
    const onDone = vi.fn()

    render(<CrawlRequestPanel keyword="흘로망" onDone={onDone} />)

    await vi.advanceTimersByTimeAsync(5000)
    await vi.advanceTimersByTimeAsync(5000)

    expect(onDone).toHaveBeenCalledWith('흘로망')
    const callsAfterDone = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterDone)
  })

  it('status가 failed면 에러와 재시도 버튼을 보여준다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({
      keyword: '흘로망', status: 'failed', error: '크롤링 실패했습니다',
    })

    render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)
    await vi.advanceTimersByTimeAsync(5000)

    expect(await screen.findByText(/크롤링 실패했습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })
})
```

- [ ] **Step 6: 테스트 실행해서 실패 확인 (`CrawlRequestPanel`)**

Run: `npm test` (frontend 디렉토리)
Expected: `Failed to resolve import "./CrawlRequestPanel.jsx"`

- [ ] **Step 7: `frontend/src/components/CrawlRequestPanel.jsx` 작성**

```jsx
import { useEffect, useRef, useState } from 'react'
import { requestCrawl, fetchCrawlStatus } from '../api.js'

const POLL_INTERVAL_MS = 5000

export default function CrawlRequestPanel({ keyword, onDone }) {
  const [status, setStatus] = useState('queued')
  const [error, setError] = useState(null)
  const timerRef = useRef(null)

  function startPolling() {
    timerRef.current = setInterval(async () => {
      const data = await fetchCrawlStatus(keyword)
      setStatus(data.status)
      if (data.status === 'done') {
        clearInterval(timerRef.current)
        onDone(keyword)
      } else if (data.status === 'failed') {
        clearInterval(timerRef.current)
        setError(data.error)
      }
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    requestCrawl(keyword).then(() => {
      startPolling()
    })
    return () => clearInterval(timerRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword])

  function handleRetry() {
    setError(null)
    setStatus('queued')
    requestCrawl(keyword).then(() => startPolling())
  }

  if (error) {
    return (
      <div>
        <p>'{keyword}' 수집에 실패했습니다: {error}</p>
        <button onClick={handleRetry}>다시 시도</button>
      </div>
    )
  }

  return <p>'{keyword}' 수집 중입니다... (보통 수십 분 소요, 잠시 기다려 주세요)</p>
}
```

- [ ] **Step 8: 테스트 실행해서 통과 확인**

Run: `npm test` (frontend 디렉토리)
Expected: 전부 통과

- [ ] **Step 9: 커밋**

```bash
git add frontend/src/components/KeywordSelector.jsx frontend/src/components/KeywordSelector.test.jsx frontend/src/components/CrawlRequestPanel.jsx frontend/src/components/CrawlRequestPanel.test.jsx
git commit -m "feat(frontend): KeywordSelector + CrawlRequestPanel 컴포넌트"
```

---

### Task 5: `AnalysisPanel` 컴포넌트

**Files:**
- Create: `frontend/src/components/AnalysisPanel.jsx`, `frontend/src/components/AnalysisPanel.test.jsx`

**Interfaces:**
- Consumes: `api.js`의 `fetchTrend`, `analyzeKeyword`(Task 3)
- Produces: `AnalysisPanel({ keyword })` — `keyword`가 바뀔 때마다 트렌드+분석을 동시 호출, 로딩/에러/결과 표시

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/src/components/AnalysisPanel.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import AnalysisPanel from './AnalysisPanel.jsx'
import * as api from '../api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AnalysisPanel', () => {
  it('로딩 중에는 로딩 표시를 보여준다', () => {
    vi.spyOn(api, 'fetchTrend').mockReturnValue(new Promise(() => {}))
    vi.spyOn(api, 'analyzeKeyword').mockReturnValue(new Promise(() => {}))
    render(<AnalysisPanel keyword="야르" />)
    expect(screen.getByText(/분석 중/)).toBeInTheDocument()
  })

  it('성공하면 분석 결과와 트렌드를 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue({ status: '유행 중', final_z: 1.2 })
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({ result: '야르는 ~라는 뜻입니다', trend: null })

    render(<AnalysisPanel keyword="야르" />)

    expect(await screen.findByText('야르는 ~라는 뜻입니다')).toBeInTheDocument()
    expect(screen.getByText(/유행 중/)).toBeInTheDocument()
  })

  it('트렌드 데이터가 없으면(null) 트렌드 표시 없이 분석 결과만 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({ result: '분석 결과', trend: null })

    render(<AnalysisPanel keyword="야르" />)

    expect(await screen.findByText('분석 결과')).toBeInTheDocument()
    expect(screen.queryByText(/유행/)).not.toBeInTheDocument()
  })

  it('분석 실패시 에러 메시지를 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockRejectedValue(new Error('데이터를 찾을 수 없습니다'))

    render(<AnalysisPanel keyword="야르" />)

    expect(await screen.findByText('데이터를 찾을 수 없습니다')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `npm test` (frontend 디렉토리)
Expected: `Failed to resolve import "./AnalysisPanel.jsx"`

- [ ] **Step 3: `frontend/src/components/AnalysisPanel.jsx` 작성**

```jsx
import { useEffect, useState } from 'react'
import { fetchTrend, analyzeKeyword } from '../api.js'

export default function AnalysisPanel({ keyword }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [trend, setTrend] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setResult(null)
    setTrend(null)

    Promise.all([fetchTrend(keyword), analyzeKeyword(keyword)])
      .then(([trendData, analysisData]) => {
        if (cancelled) return
        setTrend(trendData)
        setResult(analysisData.result)
      })
      .catch((e) => {
        if (cancelled) return
        setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [keyword])

  if (loading) return <p>분석 중...</p>
  if (error) return <p role="alert">{error}</p>

  return (
    <div>
      {trend && <p>트렌드: {trend.status}</p>}
      <p>{result}</p>
    </div>
  )
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `npm test` (frontend 디렉토리)
Expected: 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/components/AnalysisPanel.jsx frontend/src/components/AnalysisPanel.test.jsx
git commit -m "feat(frontend): AnalysisPanel 컴포넌트 (트렌드+분석 결과 표시)"
```

---

### Task 6: `SourceFilter` + `RagPanel` 컴포넌트

**Files:**
- Create: `frontend/src/components/SourceFilter.jsx`
- Create: `frontend/src/components/RagPanel.jsx`, `frontend/src/components/RagPanel.test.jsx`

**Interfaces:**
- Consumes: `api.js`의 `askRag`, `AVAILABLE_SOURCES`(Task 3)
- Produces:
  - `SourceFilter({ selected, onChange })` — `selected: string[]`, `onChange(selected: string[])`. `AVAILABLE_SOURCES` 각각에 대한 체크박스.
  - `RagPanel({ keyword })` — 소스 필터 + 질문 입력 + 전송 → `askRag(keyword, question, selectedSources)` → 로딩/에러/답변+출처 표시

- [ ] **Step 1: `frontend/src/components/SourceFilter.jsx` 작성 (테스트는 이 컴포넌트를 사용하는 `RagPanel.test.jsx`에서 통합 검증 — 체크박스 목록 자체는 `AVAILABLE_SOURCES`를 그대로 렌더링하는 단순 컴포넌트라 별도 단위 테스트 없이 진행)**

```jsx
import { AVAILABLE_SOURCES } from '../api.js'

export default function SourceFilter({ selected, onChange }) {
  function toggle(source) {
    if (selected.includes(source)) {
      onChange(selected.filter((s) => s !== source))
    } else {
      onChange([...selected, source])
    }
  }

  return (
    <fieldset>
      <legend>출처 필터</legend>
      {AVAILABLE_SOURCES.map((source) => (
        <label key={source}>
          <input
            type="checkbox"
            checked={selected.includes(source)}
            onChange={() => toggle(source)}
          />
          {source}
        </label>
      ))}
    </fieldset>
  )
}
```

- [ ] **Step 2: 실패하는 테스트 작성 — `RagPanel`**

`frontend/src/components/RagPanel.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import RagPanel from './RagPanel.jsx'
import * as api from '../api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RagPanel', () => {
  it('기본적으로 모든 소스가 체크된 상태로 시작한다', () => {
    render(<RagPanel keyword="야르" />)
    for (const source of api.AVAILABLE_SOURCES) {
      expect(screen.getByLabelText(source)).toBeChecked()
    }
  })

  it('질문을 보내면 체크된 소스만 askRag에 전달된다', async () => {
    const askRagSpy = vi.spyOn(api, 'askRag').mockResolvedValue({
      answer: '야르는 감탄사입니다', sources: [{ title: '제목', url: 'https://example.com' }], trend: null,
    })

    render(<RagPanel keyword="야르" />)

    await userEvent.click(screen.getByLabelText('tavily'))  // tavily 체크 해제
    await userEvent.type(screen.getByRole('textbox', { name: '질문' }), '무슨 뜻이야?')
    await userEvent.click(screen.getByRole('button', { name: '질문하기' }))

    expect(askRagSpy).toHaveBeenCalledWith(
      '야르', '무슨 뜻이야?',
      expect.not.arrayContaining(['tavily']),
    )
    expect(await screen.findByText('야르는 감탄사입니다')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '제목' })).toHaveAttribute('href', 'https://example.com')
  })

  it('실패하면 에러 메시지를 보여준다', async () => {
    vi.spyOn(api, 'askRag').mockRejectedValue(new Error('검색 결과가 없습니다'))

    render(<RagPanel keyword="야르" />)
    await userEvent.type(screen.getByRole('textbox', { name: '질문' }), '아무거나')
    await userEvent.click(screen.getByRole('button', { name: '질문하기' }))

    expect(await screen.findByText('검색 결과가 없습니다')).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: 테스트 실행해서 실패 확인**

Run: `npm test` (frontend 디렉토리)
Expected: `Failed to resolve import "./RagPanel.jsx"`

- [ ] **Step 4: `frontend/src/components/RagPanel.jsx` 작성**

```jsx
import { useState } from 'react'
import { askRag, AVAILABLE_SOURCES } from '../api.js'
import SourceFilter from './SourceFilter.jsx'

export default function RagPanel({ keyword }) {
  const [selectedSources, setSelectedSources] = useState([...AVAILABLE_SOURCES])
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [sources, setSources] = useState([])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    setAnswer(null)
    try {
      const data = await askRag(keyword, question, selectedSources)
      setAnswer(data.answer)
      setSources(data.sources)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <SourceFilter selected={selectedSources} onChange={setSelectedSources} />
      <form onSubmit={handleSubmit}>
        <label htmlFor="rag-question">질문</label>
        <input
          id="rag-question"
          aria-label="질문"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="더 궁금한 게 있으세요?"
        />
        <button type="submit" disabled={loading}>질문하기</button>
      </form>
      {loading && <p>답변 생성 중...</p>}
      {error && <p role="alert">{error}</p>}
      {answer && (
        <div>
          <p>{answer}</p>
          <ul>
            {sources.map((s, i) => (
              <li key={i}>
                <a href={s.url}>{s.title}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: 테스트 실행해서 통과 확인**

Run: `npm test` (frontend 디렉토리)
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/components/SourceFilter.jsx frontend/src/components/RagPanel.jsx frontend/src/components/RagPanel.test.jsx
git commit -m "feat(frontend): SourceFilter + RagPanel 컴포넌트"
```

---

### Task 7: `App.jsx` 통합 + 수동 검증

**Files:**
- Modify: `frontend/src/App.jsx` (Task 2의 플레이스홀더를 실제 내용으로 교체)
- Modify: `frontend/src/App.test.jsx` (플레이스홀더 테스트를 실제 통합 테스트로 교체)

**Interfaces:**
- Consumes: `KeywordSelector`, `CrawlRequestPanel`, `AnalysisPanel`, `RagPanel`(Task 4~6), `fetchKeywords`(Task 3)
- Produces: 완성된 최상위 `App` 컴포넌트

- [ ] **Step 1: 실패하는(교체된) 테스트 작성**

`frontend/src/App.test.jsx` 전체 교체:

```jsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import App from './App.jsx'
import * as api from './api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App', () => {
  it('시작 시 키워드 목록을 불러와서 KeywordSelector에 전달한다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    render(<App />)
    expect(await screen.findByRole('textbox')).toBeInTheDocument()
  })

  it('기존 키워드를 검색하면 AnalysisPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({ result: '야르 분석 결과', trend: null })

    render(<App />)
    const input = await screen.findByRole('textbox')
    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('야르 분석 결과')).toBeInTheDocument()
  })

  it('없는 키워드를 검색하면 CrawlRequestPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue([])
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({ keyword: '흘로망', status: 'queued' })

    render(<App />)
    const input = await screen.findByRole('textbox')
    await userEvent.type(input, '흘로망')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText(/수집 중입니다/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 테스트 실행해서 실패 확인**

Run: `npm test` (frontend 디렉토리)
Expected: 최소 2번째/3번째 테스트 실패(현재 `App.jsx`가 플레이스홀더라 `KeywordSelector` 등을 렌더링 안 함)

- [ ] **Step 3: `frontend/src/App.jsx` 전체 교체**

```jsx
import { useEffect, useState } from 'react'
import { fetchKeywords } from './api.js'
import KeywordSelector from './components/KeywordSelector.jsx'
import CrawlRequestPanel from './components/CrawlRequestPanel.jsx'
import AnalysisPanel from './components/AnalysisPanel.jsx'
import RagPanel from './components/RagPanel.jsx'

export default function App() {
  const [keywords, setKeywords] = useState([])
  const [selectedKeyword, setSelectedKeyword] = useState(null)
  const [pendingKeyword, setPendingKeyword] = useState(null)

  useEffect(() => {
    fetchKeywords().then(setKeywords)
  }, [])

  function handleSelect(keyword) {
    setPendingKeyword(null)
    setSelectedKeyword(keyword)
  }

  function handleNewKeyword(keyword) {
    setSelectedKeyword(null)
    setPendingKeyword(keyword)
  }

  function handleCrawlDone(keyword) {
    setKeywords((prev) => [...prev, keyword])
    setPendingKeyword(null)
    setSelectedKeyword(keyword)
  }

  return (
    <div>
      <h1>mimori — 밈/신조어 검색</h1>
      <KeywordSelector keywords={keywords} onSelect={handleSelect} onNewKeyword={handleNewKeyword} />
      {pendingKeyword && <CrawlRequestPanel keyword={pendingKeyword} onDone={handleCrawlDone} />}
      {selectedKeyword && (
        <>
          <AnalysisPanel keyword={selectedKeyword} />
          <RagPanel keyword={selectedKeyword} />
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 4: 테스트 실행해서 통과 확인**

Run: `npm test` (frontend 디렉토리)
Expected: 전부 통과

- [ ] **Step 5: 전체 프론트엔드 테스트 스위트 회귀 확인**

Run: `npm test` (frontend 디렉토리, 전체 파일 대상으로 이미 실행됨 — `api.test.js`, `KeywordSelector.test.jsx`, `CrawlRequestPanel.test.jsx`, `AnalysisPanel.test.jsx`, `RagPanel.test.jsx`, `App.test.jsx` 전부 포함)
Expected: 전부 통과

- [ ] **Step 6: 프로덕션 빌드 재확인**

Run: `npm run build` (frontend 디렉토리)
Expected: 에러 없이 `dist/` 생성

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/App.jsx frontend/src/App.test.jsx
git commit -m "feat(frontend): App.jsx 전체 통합 (키워드 선택/수집요청/분석/RAG)"
```

- [ ] **Step 8: 수동 통합 검증 (Docker 데몬 필요 — 로컬에 없으면 이 단계는 건너뛰고 보고)**

```bash
docker compose up -d --build web api mongo qdrant
```
브라우저로 `http://localhost` 접속해서:
1. 키워드 목록이 뜨는지
2. 기존 키워드 검색 → 분석 결과 표시되는지
3. 없는 키워드 검색 → "수집 요청" 흐름이 뜨는지(실제로 끝까지 기다릴 필요는 없음, 화면 전환만 확인)
4. 소스 필터 체크/해제 후 질문 → 답변 오는지

Docker 데몬이 없는 샌드박스 환경이면 이 단계는 건너뛰고 "Docker 데몬 없어 수동 검증 못 함"으로 보고 — Task 9(EC2)와 마찬가지로 실제 배포 후 사람이 확인.

---

## 스펙 커버리지 체크 (self-review)

- ✅ 단일 페이지, 키워드 선택→분석→질문 흐름 — Task 4,5,6,7
- ✅ nginx 리버스프록시로 CORS 불필요, 상대경로 fetch — Task 2, 3
- ✅ 수집 요청 UI + 폴링 + 실패/재시도 — Task 4(`CrawlRequestPanel`)
- ✅ 소스 필터 체크박스 → `/api/rag`로 전달 — Task 1(백엔드), Task 6(`SourceFilter`/`RagPanel`)
- ✅ 로딩/에러 표시 원칙(백엔드 error 메시지 그대로 표시) — Task 5, 6에서 일관 적용
- ✅ gunicorn/nginx 타임아웃 정합 — Task 2 Step 10 주석에 명시
- ⚠️ 크롤 요청 UI 스펙의 "수집 요청 버튼"은 실제로는 `CrawlRequestPanel`이 마운트되자마자 자동으로 `requestCrawl`을 호출하는 방식으로 구현했다(프론트 스펙의 "[네, 요청합니다] 버튼" 문구와 약간 다름) — `KeywordSelector`가 새 키워드 감지 시점에 이미 사용자가 "검색"을 눌러 의도를 표현했다고 보고, 확인 버튼을 한 번 더 넣지 않았다. 확인 버튼이 필요하면 `App.jsx`의 `pendingKeyword` 분기에 별도 확인 버튼을 추가하는 작은 후속 수정으로 가능.

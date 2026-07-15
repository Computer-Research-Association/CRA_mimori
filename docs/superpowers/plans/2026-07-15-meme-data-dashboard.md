# 밈 데이터 대시보드 (정적 웹사이트) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수집된 밈 키워드 목록, 키워드별 수집 통계(소스별 분포), 이미 완료된 LLM 분석 결과를 하나의 `data.json` 스냅샷으로 뽑아내고, 이를 읽어 화면에 보여주는 정적 웹페이지(`dashboard/public/`)를 만든다.

**Architecture:** 기존 `main.py` / `preprocess_main.py` / `embed_main.py` / `analyze_main.py`와 동일한 "루트 진입점 스크립트 + 전용 패키지" 패턴. `dashboard/export.py`가 MongoDB에서 키워드별 통계를 모으고, 이미 임베딩된 키워드는 기존 `analysis/pipeline.py`의 함수를 그대로 재사용해 LLM 분석까지 돌린 뒤 `dashboard/public/data.json`으로 저장한다. `dashboard/public/`의 순수 HTML/CSS/JS가 이 JSON을 `fetch`해서 렌더링한다 (백엔드 서버 없음, 브라우저 JS만으로 동작).

**Tech Stack:** Python 3.11, pymongo (`DB/mongo_client.py` 재사용), 기존 `analysis/pipeline.py` (Qdrant + Ollama 재사용), 바닐라 HTML/CSS/JS (프레임워크 없음).

## Global Constraints

- Python 3.11 고정 (`pyproject.toml`: `requires-python = ">=3.11,<3.12"`)
- 모든 스크립트는 저장소 루트에서 실행하는 것을 전제로 함 (`python dashboard_main.py`), 기존 `*_main.py`처럼 `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`로 시작
- Windows 콘솔 한글 출력 깨짐 방지를 위해 진입점 스크립트에서 `sys.stdin/stdout/stderr.reconfigure(encoding="utf-8")` (win32에서만) — 기존 `*_main.py`들과 동일 패턴
- 이 프로젝트는 자동화된 테스트 프레임워크를 쓰지 않음 — 각 태스크는 실제 스크립트를 직접 실행해 눈으로/assert로 확인
- 패키지 이름은 `dashboard` (표준 라이브러리 `site` 모듈과 겹치는 `site`라는 이름은 쓰지 않음 — 스펙에 명시된 이유)
- `data.json`은 `ensure_ascii=False`로 저장 (한글 그대로, 이스케이프 안 함)
- **커밋은 사용자가 직접 진행함 — 각 태스크에 git commit 스텝을 넣지 않는다. 실행자는 코드 작성/검증까지만 하고, 커밋은 사용자에게 맡긴다.**
- 새 DB 접근 로직을 새로 만들지 않고 기존 `DB/mongo_client.py::get_collection()`, `analysis/pipeline.py`의 `list_analyzable_keywords`/`fetch_keyword_chunks`/`build_prompt`/`analyze`를 그대로 재사용

---

### Task 1: `dashboard` 패키지 스캐폴딩 + Mongo 통계 집계 함수

**Files:**
- Create: `dashboard/__init__.py`
- Create: `dashboard/export.py`

**Interfaces:**
- Consumes: `DB.mongo_client.get_collection() -> pymongo.collection.Collection` (기존 함수, 인자 없이 호출하면 기본 `memes` 컬렉션), `analysis.pipeline.list_analyzable_keywords() -> list[str]` (기존 함수)
- Produces: `dashboard.export.collect_keyword_stats() -> list[dict]` — Task 2가 이 함수를 씀. 각 dict 형태: `{"keyword": str, "total_count": int, "by_source": dict[str, int], "is_embedded": bool}`

- [ ] **Step 1: `dashboard/__init__.py` 생성 (빈 파일)**

`dashboard/__init__.py` 파일을 내용 없이 생성한다 (기존 `analysis/__init__.py`, `embedding/__init__.py`와 동일).

- [ ] **Step 2: `dashboard/export.py` 생성, `collect_keyword_stats` 구현**

```python
"""
dashboard/export.py
Mongo에 수집된 키워드별 통계를 집계하고, 이미 임베딩된 키워드는 기존
analysis/pipeline.py를 재사용해 LLM 분석까지 더해 dashboard/public/data.json
스냅샷으로 저장한다.
"""

from collections import defaultdict

from analysis.pipeline import list_analyzable_keywords
from DB.mongo_client import get_collection


def collect_keyword_stats() -> list[dict]:
    """memes 컬렉션 전체를 훑어 키워드별 총 개수, source별 분포, is_embedded
    여부를 집계. keyword 오름차순 정렬된 리스트로 반환.

    is_embedded는 analysis.pipeline.list_analyzable_keywords()와 동일한
    기준(해당 키워드에 is_embedded=True인 문서가 하나라도 있는지)을 그대로 쓴다."""
    collection = get_collection()
    analyzable = set(list_analyzable_keywords())

    counts: dict[str, dict] = {}
    for doc in collection.find({}, {"keyword": 1, "source": 1}):
        keyword = doc["keyword"]
        entry = counts.setdefault(
            keyword,
            {"keyword": keyword, "total_count": 0, "by_source": defaultdict(int)},
        )
        entry["total_count"] += 1
        entry["by_source"][doc.get("source", "unknown")] += 1

    results = []
    for entry in counts.values():
        entry["by_source"] = dict(entry["by_source"])
        entry["is_embedded"] = entry["keyword"] in analyzable
        results.append(entry)
    results.sort(key=lambda e: e["keyword"])
    return results
```

- [ ] **Step 3: 실제 MongoDB에 대고 동작 확인**

Run: `python -c "from dashboard.export import collect_keyword_stats; import json; print(json.dumps(collect_keyword_stats(), ensure_ascii=False, indent=2))"`

Expected: 키워드별 dict 리스트가 출력됨. 각 항목에 `keyword`, `total_count`(0보다 큰 정수), `by_source`(소스명→개수 dict), `is_embedded`(bool)가 들어있는지 확인. `is_embedded`가 `true`인 키워드가 최소 1개 이상 있어야 한다 (없으면 `python embed_main.py`를 먼저 한 번 실행).

- [ ] **Step 4: 검증**

Run: `python -c "from dashboard.export import collect_keyword_stats; s = collect_keyword_stats(); assert all(e['total_count'] == sum(e['by_source'].values()) for e in s); print('OK')"`

Expected: `OK` 출력 (각 키워드의 `total_count`가 `by_source` 값들의 합과 일치함을 확인 — 집계 로직 정합성 체크).

---

### Task 2: LLM 분석 결합 — `build_snapshot()` 구현

**Files:**
- Modify: `dashboard/export.py`

**Interfaces:**
- Consumes: `dashboard.export.collect_keyword_stats() -> list[dict]` (Task 1), `analysis.pipeline.fetch_keyword_chunks(keyword: str) -> list[str]`, `analysis.pipeline.build_prompt(keyword: str, chunks: list[str]) -> str`, `analysis.pipeline.analyze(prompt: str) -> str` (기존 함수 3개)
- Produces: `dashboard.export.build_snapshot() -> dict` — Task 3이 이 함수를 씀. 반환 형태: `{"generated_at": str, "summary": {"total_keywords": int, "total_docs": int, "analyzed_keywords": int}, "keywords": list[dict]}` (각 keyword dict에 `"analysis": str | None` 필드 추가됨)

- [ ] **Step 1: `dashboard/export.py`에 import 및 함수 추가**

파일 상단 import에 추가:

```python
from datetime import datetime, timezone

from analysis.pipeline import analyze, build_prompt, fetch_keyword_chunks
```

`collect_keyword_stats` 아래에 추가:

```python
def build_snapshot() -> dict:
    """collect_keyword_stats() 결과에 임베딩된 키워드의 LLM 분석을 더해
    data.json에 쓸 전체 스냅샷 dict를 만든다. 특정 키워드 분석이 실패해도
    나머지는 계속 진행한다."""
    keyword_stats = collect_keyword_stats()

    for entry in keyword_stats:
        entry["analysis"] = None
        if not entry["is_embedded"]:
            continue
        try:
            chunks = fetch_keyword_chunks(entry["keyword"])
            prompt = build_prompt(entry["keyword"], chunks)
            entry["analysis"] = analyze(prompt)
        except Exception as exc:
            print(f"[경고] '{entry['keyword']}' 분석 실패, 건너뜀: {exc}")

    summary = {
        "total_keywords": len(keyword_stats),
        "total_docs": sum(e["total_count"] for e in keyword_stats),
        "analyzed_keywords": sum(1 for e in keyword_stats if e["analysis"] is not None),
    }

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "summary": summary,
        "keywords": keyword_stats,
    }
```

- [ ] **Step 2: 실제 Mongo + Qdrant + Ollama에 대고 동작 확인**

Ollama가 로컬에서 실행 중이어야 한다 (`ollama list`로 확인). 키워드 개수만큼 LLM 호출이 일어나므로 몇 분 걸릴 수 있다.

Run: `python -c "from dashboard.export import build_snapshot; import json; s = build_snapshot(); print(json.dumps(s['summary'], ensure_ascii=False))"`

Expected: `{"total_keywords": N, "total_docs": M, "analyzed_keywords": K}` 형태로 출력되고, `K`(분석 완료 개수)가 0보다 큼. 콘솔에 `[경고]` 줄이 있다면 어떤 키워드가 실패했는지 확인(있어도 스크립트는 끝까지 도는 게 정상 동작).

---

### Task 3: `data.json` 저장 + `dashboard_main.py` 진입점

**Files:**
- Modify: `dashboard/export.py`
- Create: `dashboard_main.py`

**Interfaces:**
- Consumes: `dashboard.export.build_snapshot() -> dict` (Task 2)
- Produces: `dashboard.export.write_snapshot(snapshot: dict, path: str = ...) -> None`, `dashboard/public/data.json` 파일 — Task 4의 `script.js`가 이 파일을 읽음

- [ ] **Step 1: `dashboard/export.py`에 import 및 `write_snapshot` 추가**

파일 상단 import에 추가:

```python
import json
import os
```

파일 끝에 추가:

```python
_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "public", "data.json")


def write_snapshot(snapshot: dict, path: str = _OUTPUT_PATH) -> None:
    """스냅샷 dict를 UTF-8 JSON 파일로 저장한다 (한글 그대로, ensure_ascii=False).
    대상 폴더가 없으면 만든다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 2: `dashboard_main.py` 작성**

```python
"""
dashboard_main.py
수집된 밈 데이터(키워드/통계/LLM 분석)를 dashboard/public/data.json 스냅샷으로
저장한다. 그 폴더의 index.html이 이 파일을 읽어 화면에 그린다.

main.py / preprocess_main.py / embed_main.py / analyze_main.py와 동일하게
루트에서 바로 실행 가능:
    python dashboard_main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dashboard.export import build_snapshot, write_snapshot

if __name__ == "__main__":
    print("[수집 중] Mongo 통계 + LLM 분석 스냅샷 생성...")
    snapshot = build_snapshot()
    write_snapshot(snapshot)

    summary = snapshot["summary"]
    print(
        f"[완료] 키워드 {summary['total_keywords']}개, "
        f"분석 완료 {summary['analyzed_keywords']}개 -> dashboard/public/data.json"
    )
    print()
    print("미리보기 방법 (새 터미널에서):")
    print("  cd dashboard/public")
    print("  python -m http.server")
    print("  -> 브라우저로 http://localhost:8000 접속")
```

- [ ] **Step 3: 전체 파이프라인 실행 확인**

Run: `python dashboard_main.py`

Expected: `[수집 중]` → `[완료] 키워드 N개, 분석 완료 K개 -> dashboard/public/data.json` 순서로 출력됨.

- [ ] **Step 4: 생성된 JSON 파일 검증**

Run: `python -c "import json; d = json.load(open('dashboard/public/data.json', encoding='utf-8')); assert d['summary']['total_keywords'] == len(d['keywords']); assert any(k['analysis'] for k in d['keywords']); print('OK')"`

Expected: `OK` 출력 (파일이 유효한 JSON이고, 요약 개수와 실제 키워드 배열 길이가 일치하며, 분석 텍스트가 채워진 항목이 최소 1개 있음을 확인).

---

### Task 4: 정적 프론트엔드 (`index.html` / `style.css` / `script.js`)

**Files:**
- Create: `dashboard/public/index.html`
- Create: `dashboard/public/style.css`
- Create: `dashboard/public/script.js`

**Interfaces:**
- Consumes: `dashboard/public/data.json` (Task 3에서 생성, 스키마는 스펙 문서 참고: `generated_at`, `summary.{total_keywords,total_docs,analyzed_keywords}`, `keywords[].{keyword,total_count,by_source,is_embedded,analysis}`)

- [ ] **Step 1: `dashboard/public/index.html` 작성**

```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>밈 데이터 대시보드</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <header>
    <h1>밈 데이터 대시보드</h1>
    <p id="summary">불러오는 중...</p>
  </header>
  <main>
    <aside id="keyword-list"></aside>
    <section id="detail">
      <p class="placeholder">왼쪽에서 키워드를 선택하세요.</p>
    </section>
  </main>
  <script src="script.js"></script>
</body>
</html>
```

- [ ] **Step 2: `dashboard/public/style.css` 작성**

```css
:root {
  --bg: #0f1115;
  --panel: #171a21;
  --border: #262b36;
  --text: #e6e9ef;
  --muted: #9aa3b2;
  --accent: #7dd3c0;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
  background: var(--bg);
  color: var(--text);
}

header {
  padding: 24px 32px;
  border-bottom: 1px solid var(--border);
}

header h1 {
  margin: 0 0 4px;
  font-size: 1.4rem;
}

#summary {
  margin: 0;
  color: var(--muted);
  font-size: 0.9rem;
}

main {
  display: flex;
  min-height: calc(100vh - 90px);
}

#keyword-list {
  width: 240px;
  border-right: 1px solid var(--border);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
}

.keyword-item {
  display: flex;
  justify-content: space-between;
  background: var(--panel);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.95rem;
  text-align: left;
  font-family: inherit;
}

.keyword-item:hover {
  border-color: var(--accent);
}

.keyword-item .count {
  color: var(--muted);
}

#detail {
  flex: 1;
  padding: 24px 32px;
}

.placeholder, .no-analysis {
  color: var(--muted);
}

.source-list {
  list-style: none;
  padding: 0;
  margin: 12px 0;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.source-list li {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 0.85rem;
  color: var(--muted);
}

.analysis {
  white-space: pre-wrap;
  font-family: inherit;
  line-height: 1.6;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-top: 16px;
}
```

- [ ] **Step 3: `dashboard/public/script.js` 작성**

```javascript
async function main() {
  const res = await fetch("data.json");
  const data = await res.json();

  const summaryEl = document.getElementById("summary");
  summaryEl.textContent =
    `키워드 ${data.summary.total_keywords}개 · 문서 ${data.summary.total_docs}개 · 분석 완료 ${data.summary.analyzed_keywords}개`;

  const listEl = document.getElementById("keyword-list");
  const detailEl = document.getElementById("detail");

  data.keywords.forEach((entry) => {
    const item = document.createElement("button");
    item.className = "keyword-item";
    item.innerHTML = `<span>${entry.keyword}</span><span class="count">${entry.total_count}</span>`;
    item.addEventListener("click", () => showDetail(entry));
    listEl.appendChild(item);
  });

  function showDetail(entry) {
    const sourceRows = Object.entries(entry.by_source)
      .map(([source, count]) => `<li>${source}: ${count}</li>`)
      .join("");

    const analysisHtml = entry.analysis
      ? `<pre class="analysis">${entry.analysis}</pre>`
      : `<p class="no-analysis">아직 분석된 내용이 없습니다.</p>`;

    detailEl.innerHTML = `
      <h2>${entry.keyword}</h2>
      <p>총 ${entry.total_count}개 수집</p>
      <ul class="source-list">${sourceRows}</ul>
      ${analysisHtml}
    `;
  }
}

main();
```

- [ ] **Step 4: 로컬 브라우저에서 눈으로 확인**

Run: `cd dashboard/public && python -m http.server`

브라우저로 `http://localhost:8000` 접속해서 확인:
- 상단에 요약 문구(키워드/문서/분석 완료 개수)가 표시되는지
- 왼쪽에 키워드 목록과 개수가 나오는지
- 키워드 하나를 클릭하면 오른쪽에 소스별 분포와 (분석된 키워드라면) LLM 분석 전문이 나오는지
- `is_embedded: false`인 키워드를 클릭하면 "아직 분석된 내용이 없습니다"가 나오는지

서버는 확인 후 터미널에서 `Ctrl+C`로 종료.

> 이 단계에서 색감/타이포그래피 등을 더 다듬고 싶으면 `frontend-design` 스킬을 참고해도 좋다. 위 스타일은 동작하는 기본 버전이다.

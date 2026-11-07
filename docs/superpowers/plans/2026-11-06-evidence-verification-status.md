# Analysis Experience and Evidence Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 새 단어 분석의 진행 상태·중복 요청·재방문 경험을 개선하고, 자료 변화가 의미 있을 때만 답변을 갱신하며 그 상태를 화면에 표시한다.

**Architecture:** Flask API는 크롤링 작업을 키워드별로 재사용하고 사용자용 진행·대기 메타데이터를 직렬화한다. React는 기존 결과 화면에서 해당 메타데이터와 근거 검증 상태를 작게 표시한다. 백엔드의 임베딩 파이프라인은 갱신 후보를 만들고, 검증 워커는 별도 LLM 판정 없이 검색 점수와 URL 다양성으로 재생성 여부를 결정한다.

**Tech Stack:** Python, Flask, MongoDB, Qdrant, React, Vitest, 기존 LLM 워커.

**Spec:** `docs/superpowers/specs/2026-11-06-evidence-verification-design.md`

## Global Constraints

- 별도 LLM 사실검증 호출을 추가하지 않는다.
- 화면의 기존 시각 디자인과 페이지 구조를 유지한다.
- 검증 기준일은 답변 생성일이 아니라 마지막 성공 검토 시각이다.
- 변화 없음일 때 기존 답변 텍스트와 인용을 변경하지 않는다.
- 예상 시간은 범위로만 제공한다.
- 비밀값·MongoDB·Qdrant 데이터의 수동 변경을 금지한다.

## Review Focus

- 새 탭에서 동일 키워드를 열어도 별도 크롤링 작업이 만들어지지 않는지 테스트한다.
- 큐 정보가 없을 때도 진행 화면이 비어 있지 않고 보수적 안내를 보이는지 테스트한다.
- API에 검증 메타데이터가 없을 때 기존 클라이언트가 깨지지 않는지 테스트한다.
- 자료가 부족하면 출처 상한 때문에 모든 근거가 사라지지 않는지 테스트한다.
- 새 임베딩이 관련도 기준에 미달하면 완료 분석 결과가 유지되는지 테스트한다.

---

### Task 1: 크롤링 작업 재사용과 사용자 진행 메타데이터

**Files:**
- Modify: `api/routes.py`
- Modify: `scripts/crawl_request_worker.py`
- Test: `tests/test_api_crawl_request_endpoint.py`
- Test: `tests/test_crawl_request_worker.py`

**Interfaces:**
- Produces: 크롤링 상태 응답의 `display_stage`, `queue_position`, `estimated_wait_seconds`.

- [ ] **Step 1: Write failing backend tests**
- [ ] **Step 2: Run focused tests and verify expected failure**
- [ ] **Step 3: Reuse active keyword jobs and serialize progress metadata**
- [ ] **Step 4: Run focused tests and verify pass**
- [ ] **Step 5: Commit**

### Task 2: 진행 화면과 재방문 UX

**Files:**
- Modify: `frontend/src/components/CrawlRequestPanel.jsx`
- Modify: `frontend/src/components/CrawlRequestPanel.test.jsx`
- Modify: `frontend/src/App.jsx`
- Test: `frontend/src/App.test.jsx`

**Interfaces:**
- Consumes: Task 1 status fields.
- Produces: 단계·예상 시간·대기열의 한국어 사용자 표시와 완료 작업 복원.

- [ ] **Step 1: Write failing component tests**
- [ ] **Step 2: Run focused tests and verify expected failure**
- [ ] **Step 3: Render status metadata without redesigning the page**
- [ ] **Step 4: Run focused tests and verify pass**
- [ ] **Step 5: Commit**

### Task 3: 근거 변화 기반 갱신과 다양성 선택

**Files:**
- Modify: `config/config_cilent.py`
- Modify: `embedding/pipeline.py`
- Create: `scripts/verification_worker.py`
- Modify: `scheduler/scheduler.py`
- Modify: `analysis/rag_pipeline.py`
- Test: `tests/test_embedding_pipeline.py`
- Test: `tests/test_verification_worker.py`
- Test: `tests/test_facet_search_parallel.py`

**Interfaces:**
- Produces: 완료 분석의 `verification = {status, checked_at, answer_updated_at}`.

- [ ] **Step 1: Write failing tests for queued verification and diverse evidence selection**
- [ ] **Step 2: Run focused tests and verify expected failure**
- [ ] **Step 3: Queue verification candidates and select non-duplicated evidence**
- [ ] **Step 4: Implement a no-extra-LLM verification worker and scheduler registration**
- [ ] **Step 5: Run focused tests and verify pass**
- [ ] **Step 6: Commit**

### Task 4: 최신성 상태 표시와 자료 부족 안내

**Files:**
- Modify: `api/routes.py`
- Modify: `frontend/src/components/AnalysisPanel.jsx`
- Modify: `frontend/src/components/AnalysisPanel.test.jsx`
- Modify: frontend stylesheet containing `.tier-section__caption`
- Test: `tests/test_api_analyze_endpoint.py`

- [ ] **Step 1: Write failing API and component tests**
- [ ] **Step 2: Run focused tests and verify expected failure**
- [ ] **Step 3: Serialize and render verification status**
- [ ] **Step 4: Run focused tests and verify pass**
- [ ] **Step 5: Commit**

### Task 5: 전체 검증 및 배포 가능한 변경 정리

**Files:**
- Verify: backend and frontend suites

- [ ] **Step 1: Run the focused backend test suite**
- [ ] **Step 2: Run frontend tests and production build**
- [ ] **Step 3: Review diff and commit all implementation changes**

# NVIDIA Build 끊김 및 신규 수집 점검 (2026-11-09)

## 범위

LLM은 NVIDIA Build만 사용한다. 기존 NIM_KEY를 그대로 사용하며 별도 공급자나
유료 서비스로 전환하는 코드는 포함하지 않는다.

429 및 requests 연결/시간초과/청크 오류는 최대 3번 시도한다. 정상 종료 신호 없는
스트림은 결과로 확정하지 않는다. 504/401/410과 길이 제한은 같은 요청을 재시도하지 않는다.
재시도에서는 이전 시도의 미완성 텍스트를 지우고 새 답변을 누적한다.

## 신규 수집

네이버 블로그 공식 검색 API를 새 소스로 등록했다. 검색 요약임을 content_kind로
기록하며 본문 전문 수집으로 간주하지 않는다. 공통 find_keyword 관련성 검사,
중복 ID, 타임아웃과 소스별 실패 격리를 적용한다. 기본 비활성이다.

기존 운영 서버 자격증명으로 검색 API HTTP 401을 확인했다. 새 데이터 저장은 실행하지 않았다.
유효한 네이버 앱 자격증명과 '검색' 사용 권한을 확인한 다음 .env에
`NAVER_BLOG_ENABLED=true`를 설정해야 한다. 비활성 상태에서는 추가 검색 요청이 없다.

## 실제 운영 계정 실측

같은 짧은 한국어 근거 요약 요청, 최대 2048 토큰, 최대 관측 60초, 모델당 1회.
아래 결과는 장기 가용성/한국어 품질 평가가 아니다. 모델 목록에 있어도 실제 생성은
404 또는 410을 반환할 수 있으므로 목록만 보고 교체하지 않는다.

| NVIDIA 모델 | 결과 |
| --- | --- |
| minimaxai/minimax-m3.1-flash | 60.02초 내 완료하지 못해 관측 중단 |
| nvidia/nemotron-3-super-120b-a12b | HTTP 404 |
| meta/llama-3.3-70b-instruct | HTTP 410 |
| openai/gpt-oss-120b | HTTP 410 |
| qwen/qwen3-next-80b-a3b-instruct | HTTP 410 |
| nvidia/nemotron-3-nano-30b-a3b | HTTP 410 |

## 검증

기존 로컬 Postman HTTP 대역을 재사용하여 실제 ChatNVIDIA SDK의 JSON/SSE,
410, 503, 빈 답변, 길이 제한, 429 복구, 종료 신호 누락을 검증한다.
모의 응답은 실제 모델 품질이나 NVIDIA의 장기 가용성을 보장하지 않는다.
CLI 1.69.0은 status/simulation을 지원하지 않아 기존 mock run을 사용한다.

## 설정과 출처

기본 LLM은 NVIDIA Build이며 `ANALYSIS_MODEL`, `ANALYSIS_BASE_URL`, `NIM_KEY`로 설정한다.
`ANALYSIS_TIMEOUT_SECONDS`로 요청의 연결/읽기 제한을 조정할 수 있다.

- https://docs.api.nvidia.com/nim/docs/api-quickstart
- https://developers.naver.com/docs/serviceapi/search/blog/blog.md

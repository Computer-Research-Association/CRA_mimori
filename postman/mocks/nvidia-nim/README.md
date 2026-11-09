# 로컬 NVIDIA 전송 검증

실제 SDK가 JSON과 SSE를 처리하는 경로를 검증한다. 모델의 응답 품질이나 공급자의 가용성을 보장하는 대역은 아니다. 공식 채팅 응답 형식과 운영에서 관측한 410을 기준으로 작성했고, 503과 빈 응답은 장애 주입 사례다. 외부 워크스페이스의 모의 서버는 검색하지 않았다.

```sh
postman mock run postman/mocks/nvidia-nim --port 4617 --no-history --output ndjson
NIM_MOCK_URL=http://127.0.0.1:4617 uv run --with pytest python -m pytest tests/test_nim_http_transport.py -q
```

검증: 실제 HTTP 스트리밍·요청 설정 전달, 410 재시도 금지, 503 최대 3회 재시도, 빈 답변 오류. 실제 키 대신 로컬 테스트 문자열을 사용하며 인증 헤더와 프롬프트를 로그에 저장하지 않는다. `/__control`과 `/__calls`는 이 로컬 검증용으로만 존재한다. 외부에 배포하지 않는다.

이 환경의 Postman CLI는 1.69.0으로 최신 스킬의 `status`·`simulation` 명령을 지원하지 않는다. 작업에 필요한 단일 HTTP 의존성은 지원되는 로컬 `mock run`으로 검증했다. 모델 가용성과 품질은 별도로 운영 계정에서 실제 생성해 확인한다.

2026-11-09 확장: NVIDIA SDK로 429 첫 요청 실패 후 복구, 종료 신호 누락 시
최대 3회 후 실패, 일반 JSON 완료도 검증한다. 총 8개 전송 시나리오를 실행한다.

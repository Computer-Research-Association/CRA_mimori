"""
judge.py
NVIDIA(ChatNVIDIA)로 (질문, 청크) 관련도를 0/1/2로 채점한다.

- gold 라벨이 없으므로 LLM 판정을 정답으로 삼아 지표(Precision/nDCG/MRR)를 계산한다.
- 세 검색 방식이 같은 청크를 뽑는 경우가 많으므로, (query_id, chunk_id) 단위로
  한 번만 채점하고 JSON 캐시에 저장한다 — 방식 간 편향 없이 호출 수도 아낀다.
- 판정은 일관성이 중요하므로 temperature=0으로 호출한다(분석 파이프라인과 다름).

관련도 척도:
    0 = 무관       (질문에 답하는 데 도움 안 됨)
    1 = 부분 관련  (키워드/주제는 겹치나 질문에 직접 답하진 않음)
    2 = 명확히 관련(질문에 직접 답이 되는 내용 포함)
"""

import hashlib
import json
import os
import re
import time

from langchain_nvidia_ai_endpoints import ChatNVIDIA

from config.config_cilent import NIM_KEY

# 판정용 모델. 분석 파이프라인과 동일 계열을 쓰되 필요하면 여기서 바꾼다.
JUDGE_MODEL = "deepseek-ai/deepseek-v4-flash"

_RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
_CACHE_PATH = os.path.join(_RESULTS_DIR, "judge_cache.json")

# NIM 무료 엔드포인트는 혼잡 시 503(ResourceExhausted)/529(Overloaded)를 자주 낸다 → 재시도로 흡수.
_MAX_RETRIES = 6
_BACKOFF_BASE_SEC = 3          # 3, 6, 12, 24, 48, 60(캡) 초로 늘려가며 재시도
_BACKOFF_CAP_SEC = 60
_RETRYABLE_MARKERS = (
    "503", "502", "504", "429", "529",
    "ResourceExhausted", "Service Unavailable", "Overloaded", "temporarily overloaded",
)

_PROMPT = """당신은 검색 결과의 관련도를 매기는 평가자입니다.
아래 [질문]에 답하는 데 [문서]가 얼마나 관련 있는지 0, 1, 2 중 하나로만 판정하세요.

- 0 = 무관: 질문에 답하는 데 전혀 도움이 되지 않음
- 1 = 부분 관련: 키워드나 주제는 겹치지만 질문에 직접 답하지는 않음
- 2 = 명확히 관련: 질문에 직접적인 답이 되는 내용을 포함

반드시 숫자 하나(0, 1, 2)만 출력하세요. 다른 말은 하지 마세요.

[질문]
{question}

[문서]
{chunk}

관련도(0/1/2):"""

# 프롬프트가 바뀌면 옛 라벨을 재사용하면 안 되므로 캐시 키에 프롬프트 해시를 섞는다.
_PROMPT_VERSION = hashlib.md5(_PROMPT.encode("utf-8")).hexdigest()[:8]


class Judge:
    """관련도 판정기. 캐시를 로드/저장하며 (query_id, chunk_id) 단위로 채점."""

    def __init__(self, model: str = JUDGE_MODEL, cache_path: str = _CACHE_PATH):
        self.cache_path = cache_path
        self.model = model
        self.cache: dict[str, int] = self._load_cache()
        # 파싱 실패 관측용 카운터. 실패가 많으면 "다 비슷하다"는 결론이 조용히 오염되므로
        # 마지막에 [파싱 실패 N / 전체 M]을 반드시 노출한다.
        self.parse_failures = 0
        self.total_scored = 0
        self.client = ChatNVIDIA(
            model=model,
            api_key=NIM_KEY,
            temperature=0,
            # 추론 계열 모델이 앞에 토큰을 흘려도 숫자가 잘리지 않도록 여유를 준다.
            max_completion_tokens=16,
            timeout=6000,
        )

    def _load_cache(self) -> dict[str, int]:
        if os.path.exists(self.cache_path):
            with open(self.cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_cache(self) -> None:
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)

    def _cache_key(self, query_id: str, chunk_id: str) -> str:
        # 모델·프롬프트가 바뀌면 다른 키가 되어 옛 라벨을 재사용하지 않는다.
        return f"{self.model}|{_PROMPT_VERSION}|{query_id}||{chunk_id}"

    def _invoke_with_retry(self, prompt: str):
        """
        일시적 서버 오류(503/429 등)면 지수 백오프로 재시도. 그 외 오류는 즉시 전파.
        langchain_nvidia는 HTTP 오류를 일반 Exception(메시지에 코드 포함)으로 던지므로
        메시지 문자열로 재시도 가능 여부를 판별한다.
        """
        for attempt in range(_MAX_RETRIES):
            try:
                return self.client.invoke([{"role": "user", "content": prompt}])
            except Exception as e:
                message = str(e)
                retryable = any(marker in message for marker in _RETRYABLE_MARKERS)
                if not retryable or attempt == _MAX_RETRIES - 1:
                    raise
                wait = min(_BACKOFF_BASE_SEC * (2 ** attempt), _BACKOFF_CAP_SEC)
                print(f"  [판정 재시도] NIM 일시 오류, {wait}초 후 재시도 ({attempt + 1}/{_MAX_RETRIES - 1}): {message[:80]}")
                time.sleep(wait)

    @staticmethod
    def _parse_score(text: str) -> int | None:
        """모델 응답에서 첫 0/1/2 숫자를 뽑는다. 못 뽑으면 None(파싱 실패) — 조용한 0 금지."""
        m = re.search(r"[012]", text or "")
        return int(m.group()) if m else None

    def score(self, query_id: str, question: str, chunk_id: str, chunk_text: str) -> int:
        """
        (query_id, chunk_id) 관련도 반환. 캐시에 있으면 그대로, 없으면 LLM 호출 후 저장.

        파싱 실패 시에는 0으로 폴백하되 캐시에 저장하지 않고(재실행 때 재시도)
        parse_failures를 올려 마지막 요약에서 실패율을 드러낸다.
        """
        key = self._cache_key(query_id, chunk_id)
        if key in self.cache:
            return self.cache[key]

        self.total_scored += 1
        prompt = _PROMPT.format(question=question, chunk=chunk_text)
        response = self._invoke_with_retry(prompt)
        parsed = self._parse_score(response.content)

        if parsed is None:
            self.parse_failures += 1
            snippet = (response.content or "").strip().replace("\n", " ")[:60]
            print(f"  [판정 파싱실패] {query_id} / {chunk_id}: 응답={snippet!r} → 0 폴백(캐시 미저장)")
            return 0

        self.cache[key] = parsed
        self._save_cache()  # 중간에 끊겨도 이미 채점한 건 재사용되도록 매번 저장
        return parsed
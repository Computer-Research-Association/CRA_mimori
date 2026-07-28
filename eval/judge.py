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

# NIM 무료 엔드포인트는 혼잡 시 503(ResourceExhausted)을 자주 낸다 → 재시도로 흡수.
_MAX_RETRIES = 6
_BACKOFF_BASE_SEC = 3          # 3, 6, 12, 24, 48, 60(캡) 초로 늘려가며 재시도
_BACKOFF_CAP_SEC = 60
_RETRYABLE_MARKERS = ("503", "502", "504", "429", "ResourceExhausted", "Service Unavailable")

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


class Judge:
    """관련도 판정기. 캐시를 로드/저장하며 (query_id, chunk_id) 단위로 채점."""

    def __init__(self, model: str = JUDGE_MODEL, cache_path: str = _CACHE_PATH):
        self.cache_path = cache_path
        self.cache: dict[str, int] = self._load_cache()
        self.client = ChatNVIDIA(
            model=model,
            api_key=NIM_KEY,
            temperature=0,
            max_completion_tokens=8,
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

    @staticmethod
    def _cache_key(query_id: str, chunk_id: str) -> str:
        return f"{query_id}||{chunk_id}"

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
    def _parse_score(text: str) -> int:
        """모델 응답에서 첫 0/1/2 숫자를 뽑는다. 못 뽑으면 0(무관) 처리."""
        m = re.search(r"[012]", text or "")
        return int(m.group()) if m else 0

    def score(self, query_id: str, question: str, chunk_id: str, chunk_text: str) -> int:
        """
        (query_id, chunk_id) 관련도 반환. 캐시에 있으면 그대로, 없으면 LLM 호출 후 저장.
        """
        key = self._cache_key(query_id, chunk_id)
        if key in self.cache:
            return self.cache[key]

        prompt = _PROMPT.format(question=question, chunk=chunk_text)
        response = self._invoke_with_retry(prompt)
        score = self._parse_score(response.content)

        self.cache[key] = score
        self._save_cache()  # 중간에 끊겨도 이미 채점한 건 재사용되도록 매번 저장
        return score
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

_PROMPT = """당신은 한국어 밈·신조어 검색 결과의 관련도를 매기는 평가자입니다.
아래 [질문]에 답하는 데 [문서]가 얼마나 관련 있는지 판정하세요.

기준:
- 0 = 무관: 질문에 답하는 데 전혀 도움이 되지 않음
- 1 = 부분 관련: 키워드나 주제는 겹치지만 질문에 직접 답하지는 않음
- 2 = 명확히 관련: 질문에 직접적인 답이 되는 내용을 포함

출력 형식 규칙:
- 점수, 인용, 이유 세 줄을 반드시 순서대로 출력할 것
- 인용: 문서에서 판정 근거가 된 핵심 구절을 원문 그대로 따옴표 안에 쓸 것. 관련 내용이 전혀 없으면 "없음" 이라고 쓸 것
- 이유: 위 인용 구절이 질문의 어떤 부분에 답하는지(또는 왜 답이 안 되는지) 설명할 것

판정 예시:

[예시 1 — 0점]
질문: 야르 무슨 뜻이야?
문서: 오늘 점심 뭐 먹을까요? 김밥이나 라면 어때요?
점수: 0
인용: "없음"
이유: 문서 전체가 식사 내용으로 야르라는 단어조차 없어 질문과 무관하다.

[예시 2 — 1점]
질문: 야르 무슨 뜻이야?
문서: 요즘 MZ세대 사이에서 야르, 킹받네, 럭키비키 같은 신조어가 유행하고 있다.
점수: 1
인용: "야르, 킹받네, 럭키비키 같은 신조어가 유행하고 있다"
이유: 야르가 언급되지만 이 구절은 유행 여부만 나열할 뿐 뜻·의미를 전혀 설명하지 않아 질문에 직접 답하지 못한다.

[예시 3 — 2점]
질문: 야르 무슨 뜻이야?
문서: '야르'는 '야 이거 레알?'의 줄임말로, 놀라움이나 감탄을 표현하는 신조어다.
점수: 2
인용: "'야르'는 '야 이거 레알?'의 줄임말로, 놀라움이나 감탄을 표현하는 신조어다"
이유: 이 구절이 야르의 어원('야 이거 레알?')과 감정적 의미(놀라움·감탄)를 직접 설명하여 '무슨 뜻인지'라는 질문에 정확히 답한다.

이제 아래를 판정하세요. 반드시 위 예시와 동일한 형식으로만 출력하세요.

[질문]
{question}

[문서]
{chunk}

점수: """

# 프롬프트가 바뀌면 옛 라벨을 재사용하면 안 되므로 캐시 키에 프롬프트 해시를 섞는다.
_PROMPT_VERSION = hashlib.md5(_PROMPT.encode("utf-8")).hexdigest()[:8]


class Judge:
    """관련도 판정기. 캐시를 로드/저장하며 (query_id, chunk_id) 단위로 채점."""

    def __init__(self, model: str = JUDGE_MODEL, cache_path: str = _CACHE_PATH):
        self.cache_path = cache_path
        self.model = model
        self.cache: dict[str, int] = self._load_cache()
        # 실패 관측용 카운터. 실패가 많으면 "다 비슷하다"는 결론이 조용히 오염되므로
        # 마지막에 [파싱 실패 N / API 실패 M / 전체 K]를 반드시 노출한다.
        self.parse_failures = 0   # 응답은 왔으나 0/1/2를 못 뽑음
        self.api_failures = 0     # NIM 과부하 등으로 재시도 소진 → 응답 자체를 못 받음
        self.total_scored = 0
        self.client = ChatNVIDIA(
            model=model,
            api_key=NIM_KEY,
            temperature=0,
            max_completion_tokens=128,
            timeout=6000,
        )

    def _load_cache(self) -> dict[str, dict]:
        if os.path.exists(self.cache_path):
            with open(self.cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 구버전(int 값) 항목은 이유 없이 score만 보유 — 새 키로 재채점될 때 자연히 교체됨
            return {k: v for k, v in raw.items() if isinstance(v, dict)}
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
        일시적 서버 오류(503/529 등)면 지수 백오프로 재시도.

        - 재시도 불가한 오류(인증 실패 등 재시도해도 소용없는 것)는 즉시 전파한다.
        - 재시도 가능한 오류를 끝까지 소진하면 전체 실행을 죽이는 대신 None을 반환한다.
          NIM 과부하로 한 청크가 안 되더라도 이미 완료한 59쿼리를 살리기 위함
          (호출부가 api_failures로 카운트하고 넘어간다).
        langchain_nvidia는 HTTP 오류를 일반 Exception(메시지에 코드 포함)으로 던지므로
        메시지 문자열로 재시도 가능 여부를 판별한다.
        """
        for attempt in range(_MAX_RETRIES):
            try:
                return self.client.invoke([{"role": "user", "content": prompt}])
            except Exception as e:
                message = str(e)
                retryable = any(marker in message for marker in _RETRYABLE_MARKERS)
                if not retryable:
                    raise
                if attempt == _MAX_RETRIES - 1:
                    print(f"  [판정 포기] NIM 재시도 {_MAX_RETRIES}회 모두 실패 — 이 청크는 판정실패로 넘어감: {message[:80]}")
                    return None
                wait = min(_BACKOFF_BASE_SEC * (2 ** attempt), _BACKOFF_CAP_SEC)
                print(f"  [판정 재시도] NIM 일시 오류, {wait}초 후 재시도 ({attempt + 1}/{_MAX_RETRIES - 1}): {message[:80]}")
                time.sleep(wait)

    @staticmethod
    def _parse_response(text: str) -> tuple[int, str, str] | None:
        """
        모델 응답에서 (점수, 인용, 이유)를 파싱한다.
        '점수: X / 인용: ... / 이유: ...' 패턴으로 먼저 시도하고,
        실패하면 숫자만 뽑아 인용·이유는 빈 문자열로 폴백한다.
        파싱 자체가 불가능하면 None 반환.
        """
        text = text or ""
        score_m = re.search(r"점수\s*:\s*([012])", text)
        quote_m = re.search(r"인용\s*:\s*(.+)", text)
        reason_m = re.search(r"이유\s*:\s*(.+)", text)
        if score_m:
            score = int(score_m.group(1))
            quote = quote_m.group(1).strip() if quote_m else ""
            reason = reason_m.group(1).strip() if reason_m else ""
            return score, quote, reason
        fallback = re.search(r"[012]", text)
        if fallback:
            return int(fallback.group()), "", ""
        return None

    def score(self, query_id: str, question: str, chunk_id: str, chunk_text: str) -> int:
        """(query_id, chunk_id) 관련도(int)를 반환한다. 캐시 우선, 없으면 LLM 호출."""
        key = self._cache_key(query_id, chunk_id)
        if key in self.cache:
            return self.cache[key]["score"]

        self.total_scored += 1
        prompt = _PROMPT.format(question=question, chunk=chunk_text)
        response = self._invoke_with_retry(prompt)

        if response is None:
            self.api_failures += 1
            return 0

        parsed = self._parse_response(response.content)

        if parsed is None:
            self.parse_failures += 1
            snippet = (response.content or "").strip().replace("\n", " ")[:60]
            print(f"  [판정 파싱실패] {query_id} / {chunk_id}: 응답={snippet!r} → 0 폴백(캐시 미저장)")
            return 0

        score, quote, reason = parsed
        self.cache[key] = {"score": score, "quote": quote, "reason": reason}
        self._save_cache()
        return score

    def get_reason(self, query_id: str, chunk_id: str) -> str | None:
        """캐시에 저장된 판정 이유를 반환한다. 채점 전이면 None."""
        key = self._cache_key(query_id, chunk_id)
        entry = self.cache.get(key)
        return entry["reason"] if entry else None

    def get_quote(self, query_id: str, chunk_id: str) -> str | None:
        """캐시에 저장된 원문 인용 구절을 반환한다. 채점 전이면 None."""
        key = self._cache_key(query_id, chunk_id)
        entry = self.cache.get(key)
        return entry.get("quote") if entry else None
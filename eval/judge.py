"""
judge.py
NVIDIA(ChatNVIDIA)로 (질문, 청크) 관련도를 0/1/2로 채점한다.

- gold 라벨이 없으므로 LLM 판정을 정답으로 삼아 지표(Precision/nDCG/MRR)를 계산한다.
- 세 검색 방식이 같은 청크를 뽑는 경우가 많으므로, (query_id, chunk_id) 단위로
  한 번만 채점하고 JSON 캐시에 저장한다 — 방식 간 편향 없이 호출 수도 아낀다.
- 판정은 일관성이 중요하므로 temperature=0으로 호출한다(분석 파이프라인과 다름).

관련도 척도(질문 유형별로 기준이 다르다 — 프롬프트 본문 참조):
    0 = 정보 없음  (단순 언급·반응·질문글·페이지 잡동사니 등, 설명이 전혀 없음)
    1 = 부족한 답  (다른 유형의 정보만 있거나, 예고·목차뿐이거나, 답이 불확실함)
    2 = 확정적 답  (질문한 유형에 실질적·확정적으로 답함)

프롬프트 이력(2026-08-04, 사람 라벨 100쌍 기준 gold 검증):
    현재 프롬프트는 4개 후보 중 A/B 테스트로 고른 것이다(eval/judge_eval.py ab).
      A(유형 구분 없는 초기안)  κ=0.519 — 유래 0.187 / 사용맥락 0.724
      B("신조어를 다루면 최소 1점") κ=0.407 — 잡음 청크까지 1점을 줘 실패
      C("설명 없으면 0점"만 강조)  κ=0.436 — 0점 과잉(35개, 사람은 17개)
      D = 현재                   κ=0.529 — 유래 0.385로 개선, 0점 19개로 사람과 근접
    A와 D의 차이는 통계적으로 유의하지 않지만(ΔAcc=0.00, CI[-0.07,+0.06]),
    가장 취약하던 '유래' 유형을 두 배로 끌어올렸고 판단 기준이 명시적이라 D를 택했다.
    남은 불일치는 주로 '사용맥락' 라벨의 흔들림(단순 사용 예를 0/1 중 무엇으로 볼지)에서
    오므로, 프롬프트를 더 손대기 전에 기준 확정과 test-retest가 필요하다.
"""

import hashlib
import json
import os
import re
import threading
import time

from google import genai
from google.genai import types

from config.config_cilent import GEMINI_API_KEY

# 판정용 모델. gemini-3.5-flash는 무료 티어 일일 한도가 20건뿐이라 대량 채점에 못 씀.
# lite-latest는 무료 한도가 훨씬 넉넉해 build(수백 콜)/score를 감당할 수 있다.
JUDGE_MODEL = "gemini-flash-lite-latest"

_RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
_CACHE_PATH = os.path.join(_RESULTS_DIR, "judge_cache.json")

# Gemini API는 429(RESOURCE_EXHAUSTED/할당량)·503(서버 오류)을 낼 수 있다 → 재시도로 흡수.
_MAX_RETRIES = 6
_BACKOFF_BASE_SEC = 3          # 3, 6, 12, 24, 48, 60(캡) 초로 늘려가며 재시도
_BACKOFF_CAP_SEC = 60
_RETRYABLE_MARKERS = (
    "503", "502", "504", "429", "529",
    "ResourceExhausted", "RESOURCE_EXHAUSTED", "Service Unavailable",
    "Overloaded", "temporarily overloaded", "quota",
)

_PROMPT = """당신은 한국어 밈·신조어 검색 결과의 관련도를 매기는 평가자입니다.
아래 [질문]에 답하는 데 [문서]가 얼마나 관련 있는지 판정하세요.

먼저 [질문]이 무엇을 묻는지 세 유형 중 하나로 분류하고, 그 유형의 기준으로 채점하세요.

■ 유형 분류
- 뜻     : "무슨 뜻이야?", "뜻" 처럼 의미·정의를 묻는 질문
- 유래   : "어디서 유래했어?" 처럼 말이 생겨난 배경을 묻는 질문
- 사용맥락: "어떤 상황에서 사용해?" 처럼 언제·어떻게 쓰는지를 묻는 질문

■ 2점 — 질문한 유형에 실질적이고 확정적으로 답한다
- 뜻     : 그 말의 의미·정의를 밝힌다.
- 유래   : 다음 중 하나라도 확정적으로 밝히면 2점이다.
           · 어원 — 줄임말의 원래 표현이나 결합된 단어
             (예: "내또출은 '내일 또 출근'의 줄임말")
           · 최초 출처 — 처음 쓰인 커뮤니티·방송·인물
           · 유행 계기나 시점
           ※ 최초 출처가 없어도 어원을 밝히면 2점이다.
- 사용맥락: 쓰이는 상황·대상·감정이 드러나거나, 그 말의 뜻·정의를 설명하면 2점이다.
           ※ 뜻을 알면 언제 쓰는 말인지 알 수 있으므로, 형식이 '뜻 설명'이어도
             사용맥락에 답한 것으로 본다.
             (예: "오운완은 '오늘 운동 완료'라는 뜻이다" → 2점)
           ※ 단, 그 말이 **어디서 쓰이기 시작했는지(출처·커뮤니티)만** 밝히는 것은
             유래에 해당하며 사용 상황이 아니므로 1점이다.
             (예: "오운완은 원래 디시인사이드 헬스 갤러리에서 쓰이던 말이다" → 1점)

■ 1점 — 그 말을 다루지만 답이 부족하다
- 다른 유형의 정보만 있는 경우 (질문은 유래인데 뜻만 설명 등)
- 설명하겠다는 예고·제목·목차만 있고 실제 내용은 없는 경우
  (예: "#오운완 ##무슨 뜻? ##이렇게 써요 ##예문")
- 답이 확정적이지 않은 경우 — 문서 스스로 "명확히 밝혀지지 않았다", "~로 추측된다"처럼
  불확실하게 서술하면 유형이 맞더라도 1점으로 낮춘다
- 질문의 표현과 밀접한 원말·변형을 대신 설명하는 경우
  (예: '할렐야루'를 물었는데 '할렐루야'를 설명)
- 그 말이 실제로 쓰인 모습만 보이고 설명은 없는 경우
  (예: 댓글에 "두존크 ㅋㅋㅋ"처럼 표현만 등장 → 사용 예이므로 0점이 아니라 1점)

■ 0점 — 그 말에 대한 정보가 전혀 없다
- 감상·반응만 있는 경우 ("67 67 ㅋㅋ", "그래서 뜻이 뭔데 짜증난다")
- 그 말을 궁금해하는 질문글 ("~의 유래를 알려주세요", "무슨 뜻인데요?")
- 사이트 메뉴·광고·목록 등 페이지 잡동사니
- 질문의 신조어와 무관한 다른 주제의 글
※ 0점은 정보가 **전혀** 없을 때만 준다. 설명이 조금이라도 있으면 1점 이상이다.

출력 형식 규칙:
- 유형, 점수, 인용, 이유 네 줄을 반드시 순서대로 출력할 것
- 유형: 뜻 / 유래 / 사용맥락 중 하나
- 인용: 문서에서 판정 근거가 된 핵심 구절을 원문 그대로 따옴표 안에 쓸 것. 정보가 전혀 없으면 "없음" 이라고 쓸 것
- 이유: 위 구절이 그 유형의 기준을 어떻게 충족하는지(또는 왜 못 하는지) 설명할 것

판정 예시:

[예시 1 — 뜻 / 2점]
질문: 내또출 뜻
문서: 최근 인터넷에서 슬슬 돌고 있는 신조어가 하나 눈에 들어 온다. '내또출'. '내일 또 출근'을 줄인 말이다.
유형: 뜻
점수: 2
인용: "'내또출'. '내일 또 출근'을 줄인 말이다"
이유: 내또출의 의미를 확정적으로 밝히므로 뜻 기준을 충족한다.

[예시 2 — 뜻 / 1점, 예고만 있음]
질문: 야르 뜻
문서: '야르' 뜻을 알려드릴게요 !! 📚 #야르 #신조어 #mz
유형: 뜻
점수: 1
인용: "'야르' 뜻을 알려드릴게요"
이유: 뜻을 알려주겠다는 예고와 해시태그뿐이고 실제 의미 설명이 없어 답이 부족하다.

[예시 3 — 뜻 / 1점, 밀접한 다른 말]
질문: 할렐야루 무슨 뜻이야?
문서: 할렐루야(히브리어, 라틴어: Alleluja, 영어: Hallelujah)는 기독교에서 '하나님을 찬양하라'는 뜻을 나타내는 히브리어 표현이다.
유형: 뜻
점수: 1
인용: "할렐루야는 기독교에서 '하나님을 찬양하라'는 뜻을 나타내는 히브리어 표현이다"
이유: 질문의 '할렐야루'가 아니라 원말 '할렐루야'를 설명하므로 밀접하지만 직접 답은 아니다.

[예시 4 — 뜻 / 0점, 단순 언급]
질문: 밤티 뜻
문서: Jimin's knit (@knitJM). 7 likes. 모르는 사람이 보면 밤티 뜻 이건 줄ㅋ.
유형: 뜻
점수: 0
인용: "없음"
이유: '밤티'가 등장하지만 뜻에 대한 설명이 전혀 없는 단순 언급이다.

[예시 5 — 뜻 / 0점, 반응만]
질문: 67 무슨 뜻이야?
문서: 그래서 뜻이 뭔데... 잼들이 계속 67 67 거리는거 짜증난다구ㅠ 알고리즘에 자꾸 뜨나 했네
유형: 뜻
점수: 0
인용: "없음"
이유: 뜻을 궁금해하는 반응과 불평뿐이고 의미에 대한 정보가 전혀 없다.

[예시 6 — 유래 / 2점, 어원을 밝히면 2점]
질문: 내또출 어디서 유래했어?
문서: ▲ 내또출 '내일 또 출근한다'의 줄임말로, 주말의 휴식 뒤에 이어지는 월요일 출근에 대한 스트레스를 표현하는 신조어이다.
유형: 유래
점수: 2
인용: "'내일 또 출근한다'의 줄임말"
이유: 줄임말의 원래 표현을 확정적으로 밝히므로 어원에 해당해 유래 기준을 충족한다.

[예시 7 — 유래 / 1점, 불확실한 답]
질문: 야르 어디서 유래했어?
문서: 야르의 어원과 유래. '야르'의 정확한 어원은 아직 명확히 밝혀지지 않았지만, 코미디언 류근일의 입버릇에서 시작된 것으로 많이 추측해요.
유형: 유래
점수: 1
인용: "정확한 어원은 아직 명확히 밝혀지지 않았지만 … 많이 추측해요"
이유: 유래를 다루지만 문서 스스로 밝혀지지 않았다고 하며 추측에 그쳐 확정적인 답이 아니다.

[예시 8 — 유래 / 0점, 질문글과 메뉴]
질문: 젬민이 어디서 유래했어?
문서: 아하 홈 토픽 스파링 잉크 미션 멤버십 전문가 신청 나도 질문하기 ## 생활꿀팁 # 잼민이 밈의 유래에 대해서 알려주세요. 언제부턴가 잼민이
유형: 유래
점수: 0
인용: "없음"
이유: 사이트 메뉴와 유래를 묻는 질문글뿐이고 유래에 대한 설명이 전혀 없다.

[예시 9 — 사용맥락 / 2점, 뜻 설명이 상황을 담은 경우]
질문: 내또출 어떤 상황에서 사용해?
문서: ▲ 내또출 '내일 또 출근한다'의 줄임말로, 주말의 휴식 뒤에 이어지는 월요일 출근에 대한 스트레스를 표현하는 신조어이다.
유형: 사용맥락
점수: 2
인용: "주말의 휴식 뒤에 이어지는 월요일 출근에 대한 스트레스를 표현하는"
이유: 형식은 뜻 설명이지만 언제(주말 뒤 월요일) 어떤 심정으로 쓰는지가 드러나 사용 상황에 답한다.

[예시 10 — 사용맥락 / 1점, 목차만]
질문: 오운완 어떤 상황에서 사용해?
문서: 인스타공백닷컴 # 오운완 ## 무슨 뜻? ## 이렇게 써요 ## 예문
유형: 사용맥락
점수: 1
인용: "## 이렇게 써요 ## 예문"
이유: 사용법을 다루겠다는 목차만 있고 실제 상황이나 예문 내용이 없어 답이 부족하다.

[예시 11 — 사용맥락 / 1점, 사용 예만 보임]
질문: 67 어떤 상황에서 사용해?
문서: 갓데이엄~ ❤❤❤ 와우 ㅋㅋㅋ 67~~~ 갓데엄 20:00 67 67 67 67 ! ㅋ😂 뭔진 모르겠지만 춤 출때 신나보이긴하네
유형: 사용맥락
점수: 1
인용: "67~~~ … 뭔진 모르겠지만 춤 출때 신나보이긴하네"
이유: 설명은 없지만 사람들이 실제로 쓰는 모습이 보이므로 사용 예에 해당해 1점이다.

[예시 12 — 사용맥락 / 2점, 뜻 설명]
질문: 오운완 어떤 상황에서 사용해?
문서: ah 오운완 (oh-woon-wan) means 오늘 운동 완료 (today's workout is finished)
유형: 사용맥락
점수: 2
인용: "오운완 means 오늘 운동 완료"
이유: 뜻을 밝히면 언제 쓰는 말인지 알 수 있으므로 사용맥락에 답한 것으로 본다.

[예시 13 — 사용맥락 / 1점, 출처만 밝힘]
질문: 오운완 어떤 상황에서 사용해?
문서: 오운완은 원래 디시인사이드 헬스 갤러리를 비롯한 헬스 커뮤니티에서 사용되던 말입니다.
유형: 사용맥락
점수: 1
인용: "원래 디시인사이드 헬스 갤러리를 비롯한 헬스 커뮤니티에서 사용되던 말"
이유: 어디서 쓰이기 시작했는지는 유래에 해당하며, 어떤 상황에서 쓰는지는 밝히지 않는다.

이제 아래를 판정하세요. 반드시 위 예시와 동일한 형식으로만 출력하세요.

[질문]
{question}

[문서]
{chunk}

유형:"""

# 프롬프트가 바뀌면 옛 라벨을 재사용하면 안 되므로 캐시 키에 프롬프트 해시를 섞는다.
_PROMPT_VERSION = hashlib.md5(_PROMPT.encode("utf-8")).hexdigest()[:8]

# 무료 티어 시작 페이스(RPM). 실제 한도는 모델·계정마다 달라서 고정하지 않고,
# 아래 _RateLimiter가 성공/429를 보며 자동으로 조절한다. 환경변수로 조정 가능.
_START_RPM = float(os.getenv("JUDGE_RPM", "20"))


class _RateLimiter:
    """
    429를 '맞고 백오프'하지 말고 '안 맞게 페이싱'하는 AIMD 조절기(스레드 안전).

    지난 실행에서 판정 455건이 429로 죽으며 건당 최대 93초(3+6+12+24+48)를 낭비했다.
    한도 밑으로 간격을 유지하면 그 낭비가 통째로 사라져 오히려 더 빠르다.
      - 성공하면 간격을 조금씩 줄여(가속) 사용 가능한 최대 처리량을 찾아가고,
      - 429가 나면 간격을 2배로 늘려(감속) 한도 아래로 물러난다.
    """

    def __init__(self, start_rpm: float = _START_RPM):
        self._lock = threading.Lock()
        self._interval = 60.0 / max(start_rpm, 1.0)
        self._min_interval = 0.15   # 400 RPM 상한 (그 이상은 의미 없음)
        # Gemini RPM 한도는 1분 단위로 리셋되므로 그보다 긴 간격은 손해만 본다.
        self._max_interval = 6.0
        self._next_at = 0.0
        # 여러 워커가 '같은 사건'으로 동시에 429를 맞았을 때 감속이 중복 적용되는 것을
        # 막는 쿨다운. 이게 없으면 8워커 × 2배씩 = 256배로 폭주해 3 RPM까지 떨어진다.
        self._last_slowdown = 0.0
        self._slowdown_cooldown = 5.0

    def acquire(self) -> None:
        """다음 호출이 허용되는 시각까지 대기한다."""
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_at:
                    self._next_at = now + self._interval
                    return
                wait = self._next_at - now
            time.sleep(wait)

    def on_success(self) -> None:
        # 감속은 빠르게, 회복도 충분히 빠르게(3%면 한 번 튄 뒤 사실상 못 돌아온다).
        with self._lock:
            self._interval = max(self._min_interval, self._interval * 0.90)

    def on_rate_limited(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._last_slowdown < self._slowdown_cooldown:
                return   # 동시 실패는 한 번만 반영
            self._last_slowdown = now
            self._interval = min(self._max_interval, self._interval * 2.0)

    @property
    def current_rpm(self) -> float:
        with self._lock:
            return 60.0 / self._interval if self._interval else 0.0


# 프로세스 전역 페이서 — 여러 Judge 인스턴스(A/B)가 같은 API 한도를 공유하므로 하나만 둔다.
_LIMITER = _RateLimiter()


class Judge:
    """관련도 판정기. 캐시를 로드/저장하며 (query_id, chunk_id) 단위로 채점."""

    def __init__(
        self,
        model: str = JUDGE_MODEL,
        cache_path: str = _CACHE_PATH,
        prompt: str = _PROMPT,
    ):
        self.cache_path = cache_path
        self.model = model
        # 프롬프트를 인자로 받아 A/B(변형 프롬프트 비교)를 지원한다. 버전 해시를 캐시
        # 키에 섞으므로 변형끼리 라벨이 섞이지 않고 한 캐시에 공존한다(judge_eval.py ab).
        self.prompt = prompt
        self.prompt_version = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8]
        self.cache: dict[str, int] = self._load_cache()
        # 실패 관측용 카운터. 실패가 많으면 "다 비슷하다"는 결론이 조용히 오염되므로
        # 마지막에 [파싱 실패 N / API 실패 M / 전체 K]를 반드시 노출한다.
        self.parse_failures = 0   # 응답은 왔으나 0/1/2를 못 뽑음
        self.api_failures = 0     # 재시도 소진 등으로 응답 자체를 못 받음
        self.total_scored = 0
        # 병렬 채점(judge_eval)에서 캐시를 여러 스레드가 건드리므로 잠금이 필요하다.
        # 저장은 매 건마다 하면 JSON 전체 재작성이 병목이 되어 일정 건수마다 모아서 한다.
        self._cache_lock = threading.Lock()
        self._dirty = 0
        self._save_every = 20
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        # thinking_budget=0: 판정은 짧은 라벨이라 사고 토큰을 끄고 결정적으로(temperature=0).
        # 단 flash-lite 계열은 thinking_config 자체를 거부(400)하므로, 그때는 아래
        # _gen_config_basic으로 자동 강등한다(모델을 바꿔도 코드 수정 없이 돌아가게).
        self._gen_config = types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=256,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        self._gen_config_basic = types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=256,
        )

    def _load_cache(self) -> dict[str, dict]:
        if os.path.exists(self.cache_path):
            with open(self.cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 구버전(int 값) 항목은 이유 없이 score만 보유 — 새 키로 재채점될 때 자연히 교체됨
            return {k: v for k, v in raw.items() if isinstance(v, dict)}
        return {}

    def _save_cache(self) -> None:
        """디스크의 최신 내용과 병합해 저장한다.

        A/B처럼 여러 Judge가 같은 파일을 공유하면 각자 메모리에 로드한 스냅샷을 그대로
        덮어쓰게 되어, 나중에 저장한 쪽이 먼저 저장한 쪽의 판정을 통째로 지운다
        (실제로 ab 실행에서 A의 100건이 이렇게 사라졌다). 쓰기 직전에 파일을 다시 읽어
        합친 뒤 기록하면 서로의 결과를 보존할 수 있다.
        """
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        merged: dict[str, dict] = {}
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    merged = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}
            except (json.JSONDecodeError, OSError):
                merged = {}   # 파일이 깨졌으면 메모리 내용만으로 복구
        merged.update(self.cache)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.cache_path)   # 저장 중 중단돼도 기존 캐시가 남도록

    def flush(self) -> None:
        """미저장 캐시를 디스크에 기록한다. 실행이 끝나면 반드시 호출할 것."""
        with self._cache_lock:
            if self._dirty:
                self._save_cache()
                self._dirty = 0

    def _cache_key(self, query_id: str, chunk_id: str) -> str:
        # 모델·프롬프트가 바뀌면 다른 키가 되어 옛 라벨을 재사용하지 않는다.
        return f"{self.model}|{self.prompt_version}|{query_id}||{chunk_id}"

    def _invoke_with_retry(self, prompt: str):
        """
        일시적 서버 오류(503/529 등)면 지수 백오프로 재시도.

        - 재시도 불가한 오류(인증 실패 등 재시도해도 소용없는 것)는 즉시 전파한다.
        - 재시도 가능한 오류를 끝까지 소진하면 전체 실행을 죽이는 대신 None을 반환한다.
          NIM 과부하로 한 청크가 안 되더라도 이미 완료한 59쿼리를 살리기 위함
          (호출부가 api_failures로 카운트하고 넘어간다).
        google-genai는 HTTP 오류를 예외(메시지에 코드 포함)로 던지므로
        메시지 문자열로 재시도 가능 여부를 판별한다. 성공 시 응답 텍스트(str)를 반환한다.
        """
        for attempt in range(_MAX_RETRIES):
            try:
                _LIMITER.acquire()   # 한도 아래로 페이싱 — 429를 애초에 피한다
                resp = self.client.models.generate_content(
                    model=self.model, contents=prompt, config=self._gen_config
                )
                _LIMITER.on_success()
                return resp.text
            except Exception as e:
                message = str(e)
                # thinking_config 미지원 모델(flash-lite 등)은 400을 낸다 → 한 번만
                # 기본 설정으로 강등하고 같은 시도를 다시 쓴다(실패로 세지 않음).
                if "INVALID_ARGUMENT" in message and self._gen_config is not self._gen_config_basic:
                    print("  [설정 강등] 이 모델은 thinking_config 미지원 → 기본 설정으로 재시도")
                    self._gen_config = self._gen_config_basic
                    continue
                retryable = any(marker in message for marker in _RETRYABLE_MARKERS)
                if not retryable:
                    raise
                # 한도에 부딪혔으면 전역 페이스를 늦춰 이후 호출까지 함께 보호한다.
                _LIMITER.on_rate_limited()
                if attempt == _MAX_RETRIES - 1:
                    print(f"  [판정 포기] Gemini 재시도 {_MAX_RETRIES}회 모두 실패 — 이 청크는 판정실패로 넘어감: {message[:80]}")
                    return None
                wait = min(_BACKOFF_BASE_SEC * (2 ** attempt), _BACKOFF_CAP_SEC)
                print(f"  [판정 재시도] Gemini 일시 오류, {wait}초 후 재시도 ({attempt + 1}/{_MAX_RETRIES - 1}): {message[:80]}")
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
        # 프롬프트가 "점수: "로 끝나므로 모델은 보통 라벨 없이 숫자부터 이어 쓴다
        # ("2\n인용: ...\n이유: ..."). 따라서 점수는 '점수:' 형태와 선두 숫자 양쪽을
        # 인정하고, 인용·이유는 점수 표기 방식과 무관하게 각자 뽑는다.
        # (선두 숫자만 보고 곧장 폴백하면 함께 온 인용·이유를 통째로 버리게 된다.)
        score_m = re.search(r"점수\s*:\s*([012])", text) or re.match(r"\s*([012])\b", text)
        if not score_m:
            score_m = re.search(r"[012]", text)
        if not score_m:
            return None

        quote_m = re.search(r"인용\s*:\s*(.+)", text)
        reason_m = re.search(r"이유\s*:\s*(.+)", text)
        score = int(score_m.group(1) if score_m.groups() else score_m.group())
        quote = quote_m.group(1).strip() if quote_m else ""
        reason = reason_m.group(1).strip() if reason_m else ""
        return score, quote, reason

    def score(self, query_id: str, question: str, chunk_id: str, chunk_text: str) -> int:
        """(query_id, chunk_id) 관련도(int)를 반환한다. 캐시 우선, 없으면 LLM 호출."""
        key = self._cache_key(query_id, chunk_id)
        with self._cache_lock:
            hit = self.cache.get(key)
        if hit is not None:
            return hit["score"]

        self.total_scored += 1
        prompt = self.prompt.format(question=question, chunk=chunk_text)
        text = self._invoke_with_retry(prompt)

        if text is None:
            self.api_failures += 1
            return 0

        parsed = self._parse_response(text)

        if parsed is None:
            self.parse_failures += 1
            snippet = (text or "").strip().replace("\n", " ")[:60]
            print(f"  [판정 파싱실패] {query_id} / {chunk_id}: 응답={snippet!r} → 0 폴백(캐시 미저장)")
            return 0

        score, quote, reason = parsed
        with self._cache_lock:
            self.cache[key] = {"score": score, "quote": quote, "reason": reason}
            self._dirty += 1
            if self._dirty >= self._save_every:
                self._save_cache()
                self._dirty = 0
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
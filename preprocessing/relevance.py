"""
relevance.py
크롤링된 doc이 배정된 keyword의 주제인지(is_relevant)를 결정론적으로 판정한다.

이 판정기의 성격 (eval/judge.py와 구분):
  - 질문 무관. 키워드↔doc 토픽성만 본다 (질문↔청크 관련도는 eval/judge.py의 LLM).
  - 정규화 후 '전체 키워드'가 doc(제목+본문)에 통으로 등장하는지로만 판정한다.
    부분/토큰 매칭은 하지 않는다 — natepann이 "거제야호"를 "야호"로 부분매칭해
    10년 전 여행 후기를 끌어온 오염을 재현하지 않기 위함.

doc-level인 이유:
  chunk 단위로 보면 "그 밈을 다루지만 그 청크엔 키워드 글자가 안 나오는" 관련
  청크를 구조적으로 오탐한다(4차 실행의 오탐 3건이 정확히 이 유형이었다).
  그래서 제목 + 전체 청크를 이어붙인 doc 전체에서 한 번이라도 매치되면 관련으로 본다.

매치 위치(position)를 함께 반환하는 이유:
  tavily의 긴 SEO 문서는 다른 밈을 나열하다 본문 후반에 키워드를 딱 한 번
  흘릴 수 있다(주제는 여전히 무관). 그런 케이스를 걸러낼 위치 가중치가 필요한지
  라벨셋으로 판단하기 위해, 지금은 가중치를 넣지 않고 위치만 기록한다.
"""

import re
import unicodedata
from dataclasses import dataclass

# 본문 '초반'으로 볼 정규화 후 문자 수. position이 early/late로 갈리는 경계.
# 값 자체는 라벨셋 분포를 보고 조정한다(지금은 관찰용 기본값, 하드코딩 확정 아님).
EARLY_BODY_CHARS = 200

# 정규화 때 제거할 장식 문자(물결류). 키워드에 '~'가 들어있다: "거제 야호~", "좋~다~".
_DECORATIVE = "~〜﹏∼"
_WS_RE = re.compile(r"\s+")
_DECORATIVE_TABLE = {ord(c): None for c in _DECORATIVE}


def normalize(s: str) -> str:
    """매칭용 정규화: NFC → casefold → 공백 전부 제거 → 장식문자(물결) 제거.

    최소 규칙만 둔다. 놓치는 케이스가 손 라벨셋에서 드러나면 그때 규칙을 늘린다
    (자모 붕괴 등 과설계를 미리 하지 않는다)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.casefold()
    s = _WS_RE.sub("", s)
    return s.translate(_DECORATIVE_TABLE)


@dataclass(frozen=True)
class RelevanceResult:
    """판정 결과. matched=False면 position="none"."""
    keyword: str
    matched: bool
    position: str  # "title" | "early" | "late" | "none"

    @property
    def is_relevant(self) -> bool:
        return self.matched


def judge_relevance(keyword: str, title: str, body: str) -> RelevanceResult:
    """doc이 keyword 주제인지 판정한다.

    body = 그 doc의 전체 청크 텍스트를 이어붙인 것.
    제목을 먼저 보고(가장 강한 신호), 없으면 본문에서 첫 매치 위치로 early/late를 가른다.
    """
    kw = normalize(keyword)
    if not kw:
        return RelevanceResult(keyword, False, "none")

    if kw in normalize(title):
        return RelevanceResult(keyword, True, "title")

    body_norm = normalize(body)
    idx = body_norm.find(kw)
    if idx == -1:
        return RelevanceResult(keyword, False, "none")
    position = "early" if idx < EARLY_BODY_CHARS else "late"
    return RelevanceResult(keyword, True, position)


def doc_body(doc: dict) -> str:
    """cleaned_memes 문서의 전체 청크 텍스트를 이어붙인다."""
    return "\n".join(c.get("text", "") for c in doc.get("chunks", []))


def judge_doc(doc: dict) -> RelevanceResult:
    """cleaned_memes 문서 하나를 그 문서의 keyword로 판정(백필/측정용 편의 함수)."""
    return judge_relevance(
        keyword=doc.get("keyword", ""),
        title=doc.get("title", ""),
        body=doc_body(doc),
    )

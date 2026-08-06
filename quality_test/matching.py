"""
matching.py
키워드 ↔ 텍스트 매칭의 유일한 정의.

왜 한 곳에 모으나:
  크롤러(_normalize), RAG(_normalize_for_match), 미머지 judge가 각자 다른 정규화를
  갖고 있었고, 그 차이 때문에 "크롤러는 통과시켰는데 RAG는 탈락시키는" 문서가 실제로
  생겼다. 크롤러는 \\s(개행 포함)를 지우는데 RAG는 스페이스만 지웠기 때문이다 —
  크롤러가 get_text(separator="\\n")로 본문을 뽑으므로 "거제 야호~"가 텍스트에서는
  "거제\\n야호"로 들어오는 일이 흔하다.

  정의가 하나면 이런 드리프트가 구조적으로 불가능해진다.

부분 매칭을 하지 않는 이유:
  "거제야호"를 "야호"로 부분매칭해 10년 전 여행 후기를 끌어온 오염 사례가 있었다.
  정규화 후 '전체 키워드'가 통으로 등장해야만 매치로 인정한다.

position을 기록하는 이유:
  긴 SEO 문서가 다른 밈을 나열하다 본문 후반에 키워드를 한 번 흘리는 경우가 있다
  (주제는 여전히 무관). 지금은 가중치를 두지 않고 위치만 남겨, 나중에 라벨셋으로
  규칙을 정할 수 있게 한다.
"""

import re
import unicodedata
from dataclasses import dataclass

# 본문 '초반'으로 볼 정규화 후 문자 수. position이 early/late로 갈리는 경계.
EARLY_BODY_CHARS = 200

# 정규화 때 제거할 장식 문자(물결류). 키워드에 '~'가 들어있다: "거제 야호~", "좋~다~".
_DECORATIVE = "~〜﹏∼"
_DECORATIVE_TABLE = {ord(c): None for c in _DECORATIVE}
_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """매칭용 정규화: NFC → casefold → 공백류 전부 제거 → 장식 물결 제거.

    공백류는 \\s로 지운다 — 스페이스뿐 아니라 개행/탭까지 포함해야 한다.
    크롤러가 넣은 개행 때문에 매칭이 깨지는 것이 이 함수가 존재하는 이유다.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.casefold()
    s = _WS_RE.sub("", s)
    return s.translate(_DECORATIVE_TABLE)


@dataclass(frozen=True)
class Match:
    """매칭 결과. matched=False면 position="none", count=0."""
    matched: bool
    position: str  # "title" | "early" | "late" | "none"
    count: int


def find_keyword(keyword: str, title: str, body: str) -> Match:
    """title+body에서 keyword를 찾아 (매치 여부, 위치, 등장 횟수)를 반환.

    제목을 먼저 본다(가장 강한 신호). 제목에 없으면 본문에서 첫 매치 위치로
    early/late를 가른다.
    """
    kw = normalize(keyword)
    if not kw:
        return Match(False, "none", 0)

    title_norm = normalize(title)
    body_norm = normalize(body)
    count = title_norm.count(kw) + body_norm.count(kw)

    if kw in title_norm:
        return Match(True, "title", count)

    idx = body_norm.find(kw)
    if idx == -1:
        return Match(False, "none", 0)
    return Match(True, "early" if idx < EARLY_BODY_CHARS else "late", count)

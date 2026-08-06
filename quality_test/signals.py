"""
signals.py
청크 하나의 품질 신호를 계산한다.

값만 내고 좋고 나쁨은 판단하지 않는다 — 임계값은 데이터를 보고 정하는 것이라
계속 바뀌는데, 신호와 판정이 섞이면 임계값을 바꿀 때마다 데이터를 다시 계산해야 한다.
나눠두면 저장된 신호에 새 임계값을 적용해보는 것이 공짜다.
판정(임계값 적용)은 report.py / inspect.py가 config 상수를 읽어서 한다.

청크 하나만 보면 전부 계산되는 순수 함수다. 문서 간 비교가 필요한 근접 중복은
성격이 달라 여기 없다(2단계).
"""

import re

from config.config_cilent import BOILERPLATE_PHRASES
from quality_test.matching import find_keyword

_HANGUL_RE = re.compile(r"[가-힣]")
_NON_SPACE_RE = re.compile(r"\S")

# 대출/도박 스팸이 전화번호 정규식을 피하려고 쓰는 표기들.
# 실제 수집 데이터에서 관찰: "⭐ 라. 인: King 365 z ⭐", "사/고-자 신/불/자 연/체/자"
#
# _SPAM_SPLIT_RE: 한 글자씩 구분자로 쪼갠 패턴이 2회 이상 연속.
#   "사/고-자" → 사/ 고- 두 번 → 매치. 정상 한국어 문장은 이 형태가 안 나온다
#   ("습니다. 그리고"는 '다.' 한 번뿐이라 {2,}에 미달).
_SPAM_SPLIT_RE = re.compile(r"(?:[가-힣][/\-.]){2,}")
_SPAM_DECO_RE = re.compile(r"[⭐★☆✩✪◆◇▶►♠♣]")
_SPAM_SLANG_RE = re.compile(r"대출|급전|신불자|연체자|사채|카톡|텔레그램|여기로")


def hangul_ratio(text: str) -> float:
    """공백을 제외한 문자 중 한글 음절의 비율.

    공백을 분모에 넣으면 "짧은 한글 + 많은 공백"이 낮게 나와, 본문 추출 실패와
    구분이 안 된다. 국내 소스인데 이 값이 낮으면 추출 실패나 영어 SEO를 의심한다.
    """
    if not text:
        return 0.0
    non_space = len(_NON_SPACE_RE.findall(text))
    if non_space == 0:
        return 0.0
    return len(_HANGUL_RE.findall(text)) / non_space


def spam_hits(text: str) -> int:
    """대출/도박 스팸 특유의 패턴이 몇 번 나타나는지."""
    if not text:
        return 0
    return (
        len(_SPAM_SPLIT_RE.findall(text))
        + len(_SPAM_DECO_RE.findall(text))
        + len(_SPAM_SLANG_RE.findall(text))
    )


def boilerplate_hits(text: str) -> int:
    """사이트 UI 상투어가 몇 개 섞여 있는지."""
    if not text:
        return 0
    return sum(1 for phrase in BOILERPLATE_PHRASES if phrase in text)


def compute_signals(text: str, title: str, keyword: str) -> dict:
    """청크 하나의 품질 신호를 한 번에 계산해 dict로 반환.

    반환 키는 chunks.jsonl의 "signals" 필드에 그대로 들어간다.
    """
    match = find_keyword(keyword or "", title or "", text or "")
    return {
        "char_count": len(text or ""),
        "hangul_ratio": round(hangul_ratio(text or ""), 3),
        "spam_hits": spam_hits(text or ""),
        "boilerplate_hits": boilerplate_hits(text or ""),
        "match_position": match.position,
        "match_count": match.count,
    }

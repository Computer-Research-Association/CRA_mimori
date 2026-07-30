"""
questions.py
crawlers/Keywords.md의 키워드 목록을 읽어, 각 키워드에 질문 템플릿을 곱해
(keyword, question) 평가셋을 만든다.

밈/신조어에는 정답 청크 라벨(gold set)이 없으므로, 여기서 만든 질문으로 세 검색
방식을 돌린 뒤 관련도는 eval/judge.py(LLM 판정)로 매긴다.
"""

import os
from dataclasses import dataclass

from analysis.query import build_search_query

# Keywords.md 위치 (repo 루트 기준). __file__ = eval/questions.py 이므로 한 단계 위로.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORDS_PATH = os.path.join(_ROOT, "crawlers", "Keywords.md")

# 키워드마다 아래 질문(들)을 검색 쿼리로 쓴다. 키워드 자체는 여기 넣지 않고
# build_search_query(keyword, question)가 앞에 붙인다 — 서비스(rag_main)와
# 완전히 동일한 방식으로 검색 쿼리를 조립하기 위함(경로 간 로직 단일화).
# 검색 비교가 목적이라 의미/유래/사용맥락처럼 서로 결이 다른 질문을 섞어
# dense·sparse가 유리한 상황을 골고루 담는다.
#
# 주의: 주격/보조사(이/가, 은/는)를 붙이면 받침 유무에 따라 비문이 된다
# ("야르이 무슨 뜻이야?"). 게다가 "거제 야호~", "좋~다~", "67"처럼 받침 판정이
# 애매한 키워드가 섞여 있어 sparse(토큰 일치)만 체계적으로 손해를 본다.
# → 조사를 붙이지 않는 형태로 통일하고, 실제 유저 검색과 가까운 짧은 키워드형도 추가.
QUESTION_TEMPLATES: list[str] = [
    "뜻",                     # 실제 검색 분포에 가까운 짧은 키워드형
    "무슨 뜻이야?",
    "어디서 유래했어?",
    "어떤 상황에서 사용해?",
]


@dataclass(frozen=True)
class Query:
    """평가 쿼리 하나. id는 캐시/결과 조인에 쓰는 안정적인 키."""
    id: str
    keyword: str
    template: str
    question: str


def load_keywords(path: str = KEYWORDS_PATH) -> list[str]:
    """Keywords.md에서 공백/빈 줄을 제외한 키워드 리스트를 순서대로 반환."""
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def build_eval_set(
    keywords: list[str] | None = None,
    templates: list[str] = QUESTION_TEMPLATES,
) -> list[Query]:
    """
    키워드 × 템플릿으로 Query 리스트를 만든다.
    keywords가 None이면 Keywords.md 전체를 사용.
    """
    if keywords is None:
        keywords = load_keywords()

    queries: list[Query] = []
    for keyword in keywords:
        for t_idx, template in enumerate(templates):
            queries.append(
                Query(
                    id=f"{keyword}__t{t_idx}",
                    keyword=keyword,
                    template=template,
                    question=build_search_query(keyword, template),
                )
            )
    return queries
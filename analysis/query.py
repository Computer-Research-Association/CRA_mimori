"""
query.py
검색 쿼리(임베딩할 텍스트) 조립 로직의 단일 소스.

세 경로(eval / 노트북 / 서비스)가 제각각 검색 쿼리를 만들면 "평가에서는 잘
나오는데 실제 서비스에서는 안 나오는" 불일치가 생긴다. 그래서 검색에 넣을
쿼리 문자열은 반드시 이 함수 하나를 거쳐서 만든다.

주의: 이 함수가 만드는 건 '검색용 쿼리'다. LLM에게 보여줄 '원본 질문'은
그대로 두고(build_rag_prompt에 원본 question을 넘김), 벡터 검색에 넣는
텍스트에만 keyword를 붙인다. keyword가 payload 필터에만 있고 임베딩 쿼리에는
빠져 있으면, sparse가 '뜻/유래/유행' 같은 흔한 토큰에 걸려 범용 밈 설명글을
상위로 끌어올린다(서비스 경로에서 실제로 관찰된 문제).
"""


def build_search_query(keyword: str, question: str) -> str:
    """검색(임베딩)에 넣을 쿼리 문자열을 만든다: "{keyword} {question}".

    keyword를 질문 앞에 붙여, 필터뿐 아니라 벡터 쿼리에도 키워드 신호가 실리게
    한다. 한쪽이 비어 있으면 나머지만 반환한다(공백 쿼리 방지)."""
    keyword = (keyword or "").strip()
    question = (question or "").strip()
    if not keyword:
        return question
    if not question:
        return keyword
    return f"{keyword} {question}"

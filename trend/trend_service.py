"""
trend_service.py
밈 이름 하나를 받아 유행 상태를 판정하는 상위 서비스 함수.

DataLabClient(검색량 수집) + zscore(Robust Scaling 판정)를 조합한다.
크롤링 데이터(MongoDB)는 여기서 쓰지 않는다 - cold-start / 노이즈 문제로
트렌드 판정에서는 배제하고 RAG 근거 자료로만 활용하기로 결정.
"""

from trend.datalab_client import DataLabClient
from trend.zscore import get_zscore


def get_meme_trend(keyword: str, related_keywords: list[str] = None) -> dict:
    """
    밈 이름으로 유행 상태를 판정한다.

    related_keywords 기본값: ["{keyword} 뜻", "{keyword}가 뭐야"]
    (검색 변형을 합산해 실제 관심도를 더 잘 반영하기 위함)

    반환:
        {
            "keyword": str,
            "z_score": float,
            "status": str,          # 핫함 / 유행 중 / 감소 / 소멸
            "ratios": list[dict],   # [{"date", "ratio"}, ...]
        }
    """
    if related_keywords is None:
        related_keywords = [f"{keyword} 뜻", f"{keyword}가 뭐야"]

    client = DataLabClient()
    ratios = client.get_recent_ratios(keyword, related_keywords=related_keywords)

    z, status = get_zscore(ratios)

    return {
        "keyword": keyword,
        "z_score": z,
        "status": status,
        "ratios": ratios,
    }


def format_trend_context(keyword: str) -> str:
    """
    get_meme_trend()를 호출해 LLM 프롬프트에 넣을 유행 상태 텍스트를 만든다.

    데이터랩 API 키 미설정, 네트워크 오류, 검색량 데이터 없음 등 어떤 이유로든
    조회할 수 없으면 빈 문자열을 반환한다 — 호출부는 이 빈 문자열을 그대로
    프롬프트 조립에 써도 안전하며, RAG 핵심 기능은 막히지 않는다.

    ratios가 비어있는 경우(데이터랩에 해당 키워드 검색량 데이터가 전혀 없음)도
    빈 문자열을 반환한다 — get_zscore([])는 (0.0, "유행 중")을 반환하는데, 이걸
    그대로 노출하면 "데이터 없음"이 "유행 중"으로 잘못 전달되기 때문이다.
    """
    try:
        result = get_meme_trend(keyword)
    except Exception:
        return ""
    if not result["ratios"]:
        return ""
    return (
        f"[참고: 최근 검색량 기준 유행 상태 — 정성적 분석의 보조 지표로만 활용]\n"
        f"상태: {result['status']} (robust z-score: {result['z_score']:.2f})\n"
    )


if __name__ == "__main__":
    _keyword = input("키워드 입력: ").strip()
    result = get_meme_trend(_keyword)
    print(f"키워드: {result['keyword']}")
    print(f"z_score: {result['z_score']:.4f}")
    print(f"상태:    {result['status']}")
    print(f"데이터 {len(result['ratios'])}건")
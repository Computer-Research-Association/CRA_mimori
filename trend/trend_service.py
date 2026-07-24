"""
trend_service.py
밈 이름 하나를 받아 유행 상태를 판정하는 상위 서비스 함수.

두 개의 독립 신호를 robust z-score 로 환산한 뒤 앙상블한다.
  - naver_z : 네이버 데이터랩 검색량(검색 수요)  → 주 지표
  - kakao_z : 카카오 블로그/카페 언급량(콘텐츠 공급) → 보조 지표

카카오는 채널을 분리해 blog_z / cafe_z 를 따로 계산한 뒤 합친다.
  kakao_z = KAKAO_BLOG_WEIGHT * blog_z + KAKAO_CAFE_WEIGHT * cafe_z
채널 분리로 'blog 중심 확산' vs '커뮤니티(cafe) 중심 유행' 해석이 가능해진다.

앙상블: z = NAVER_WEIGHT * naver_z + KAKAO_WEIGHT * kakao_z
카카오 시계열이 비어 있거나 부족하면 자동으로 naver_z 단독으로 폴백한다.
(가중치 0.7 / 0.3 은 초기값이며 실측으로 재보정 필요.)

크롤링 데이터(MongoDB)는 여기서 쓰지 않는다 - cold-start / 노이즈 문제로
트렌드 판정에서는 배제하고 RAG 근거 자료로만 활용하기로 결정.
"""

from trend.datalab_client import DataLabClient
from trend.kakao_client import KakaoSearchClient, merge_daily_series
from trend.zscore import classify_trend, drop_incomplete_today, zscore_from_series

# 앙상블 가중치 (합 = 1.0). 초기값이며 실측으로 재보정 대상.
NAVER_WEIGHT = 0.7
KAKAO_WEIGHT = 0.3

# 카카오 채널별 가중치 (합 = 1.0). blog/cafe 를 kakao_z 로 합칠 때 사용.
KAKAO_BLOG_WEIGHT = 0.5
KAKAO_CAFE_WEIGHT = 0.5

# 카카오 시계열이 이보다 적으면 신뢰할 수 없어 naver 단독으로 폴백한다.
KAKAO_MIN_POINTS = 7

# 카카오 baseline 평균이 이 값보다 낮으면 표본 부족으로 보고 앙상블에서 제외한다.
# (데이터랩과 동일 기준 재사용) 저빈도 키워드에서 kakao_z 가 불안정해지는 걸 막는다.
KAKAO_MIN_BASELINE_AVG = 10

# 수요(naver)와 공급(kakao) z가 이 이상 반대 방향으로 벌어지면 발산 플래그를 남긴다.
# 검색은 잠잠한데 게시물만 급증 → 마케팅/도배 의심. 초기값이며 실측 후 조정.
DIVERGENCE_THRESHOLD = 10


def get_meme_trend(keyword: str, related_keywords: list[str] = None) -> dict:
    """
    밈 이름으로 유행 상태를 판정한다.

    related_keywords 기본값: ["{keyword} 뜻", "{keyword}가 뭐야"]
    (검색 변형을 합산해 실제 관심도를 더 잘 반영하기 위함)

    반환(trend_scores 컬렉션에 저장. 판정 근거 추적용 필드 포함):
        {
            "keyword": str,
            "z_score": float,        # = final_z (기존 호환 별칭)
            "final_z": float,        # 최종 판정에 쓴 z값
            "status": str,           # 핫함 / 유행 중 / 감소 / 소멸
            "naver_z": float,        # 검색 수요 z
            "kakao_z": float,        # blog_z/cafe_z 합산 (제외 시에도 참고용으로 기록)
            "blog_z": float,         # 카카오 블로그 채널 z
            "cafe_z": float,         # 카카오 카페 채널 z
            "ensemble_note": str,    # ensemble / kakao_excluded_low_baseline / kakao_excluded_no_signal
            "flags": list[str],      # 예: supply_demand_divergence
            "sources": list[str],    # 최종 판정에 실제 반영된 소스
            "ratios": list[dict],    # 네이버 데이터랩 시계열 (기존 호환)
            "kakao_counts": list[dict],   # blog+cafe 합산 시계열
            "blog_counts": list[dict],
            "cafe_counts": list[dict],
        }
    """
    if related_keywords is None:
        related_keywords = [f"{keyword} 뜻", f"{keyword}가 뭐야"]

    # ── 네이버 데이터랩 (주 지표) ──────────────────────────────────────────
    naver_ratios = DataLabClient().get_recent_ratios(
        keyword, related_keywords=related_keywords
    )
    # 미완성 당일은 판정에서 제외(완성된 최근일을 today_value로)
    naver_z = zscore_from_series(drop_incomplete_today(naver_ratios))

    # ── 카카오 블로그/카페 (보조 지표, 채널 분리) ─────────────────────────
    channels = _safe_kakao_channels(keyword)
    blog_counts, cafe_counts = channels["blog"], channels["cafe"]
    # 합산 시계열: baseline 게이트 / 신호 유무는 '합산 기준'으로 판단
    kakao_counts = merge_daily_series(blog_counts, cafe_counts)

    kakao_scored = drop_incomplete_today(kakao_counts)
    kakao_active = _has_signal(kakao_scored)
    kakao_baseline_avg = _baseline_avg(kakao_scored) if kakao_active else 0.0

    # 채널별 z (합산이 활성일 때만 계산; 아니면 0.0)
    if kakao_active:
        blog_z = zscore_from_series(drop_incomplete_today(blog_counts))
        cafe_z = zscore_from_series(drop_incomplete_today(cafe_counts))
        kakao_z = KAKAO_BLOG_WEIGHT * blog_z + KAKAO_CAFE_WEIGHT * cafe_z
    else:
        blog_z = cafe_z = kakao_z = 0.0

    # ── baseline 게이트 + 앙상블 ──────────────────────────────────────────
    if not kakao_active:
        # 카카오 표본 자체가 없음/부족 → 네이버 단독
        final_z = naver_z
        ensemble_note = "kakao_excluded_no_signal"
        sources = ["naver"]
    elif kakao_baseline_avg < KAKAO_MIN_BASELINE_AVG:
        # 카카오 baseline 평균이 낮아 kakao_z 가 불안정 → 앙상블에서 제외
        final_z = naver_z
        ensemble_note = "kakao_excluded_low_baseline"
        sources = ["naver"]
    else:
        final_z = NAVER_WEIGHT * naver_z + KAKAO_WEIGHT * kakao_z
        ensemble_note = "ensemble"
        sources = ["naver", "kakao"]

    # ── 수요/공급 발산 플래그 ─────────────────────────────────────────────
    # 발산 감지는 합산 kakao_z(평균) 대신 '지배 채널' z 기준으로 판단한다.
    # blog+cafe 를 평균내면 한 채널만 폭발한 도배 패턴이 희석돼 플래그가
    # 꺼지는 문제가 있어(예: blog_z=15.5, cafe_z=0 → kakao_z=7.75),
    # 절댓값이 큰 채널의 '부호 있는' z를 지배값으로 써서 감지한다.
    # (final_z 에 쓰는 kakao_z 평균은 그대로 두고, 감지 로직만 max 기준으로 바꿈)
    dominant_channel_z = blog_z if abs(blog_z) >= abs(cafe_z) else cafe_z

    flags: list[str] = []
    if (
        kakao_active
        and abs(naver_z - dominant_channel_z) > DIVERGENCE_THRESHOLD
        and naver_z * dominant_channel_z < 0
    ):
        flags.append("supply_demand_divergence")

    return {
        "keyword": keyword,
        "z_score": final_z,  # 기존 호환 별칭
        "final_z": final_z,
        "status": classify_trend(final_z),
        "naver_z": naver_z,
        "kakao_z": kakao_z,
        "blog_z": blog_z,
        "cafe_z": cafe_z,
        "ensemble_note": ensemble_note,
        "flags": flags,
        "sources": sources,
        "ratios": naver_ratios,
        "kakao_counts": kakao_counts,
        "blog_counts": blog_counts,
        "cafe_counts": cafe_counts,
    }


def _safe_kakao_channels(keyword: str) -> dict:
    """
    카카오 채널별 시계열을 수집하되, 키 미설정/네트워크 오류 시 빈 채널을 반환한다.
    보조 지표가 실패해도 네이버 단독 판정은 계속되어야 하므로 예외를 삼킨다.
    """
    try:
        return KakaoSearchClient().get_channel_counts(keyword)
    except Exception as exc:  # noqa: BLE001 - 보조 지표 실패는 치명적이지 않음
        print(f"[trend_service] 카카오 지표 수집 실패, 네이버 단독 진행: {exc}")
        return {"blog": [], "cafe": []}


def _has_signal(counts: list[dict]) -> bool:
    """
    카카오 시계열이 앙상블에 쓸 만한지 판단한다.
    최소 포인트 수를 채우고, 언급이 전부 0이 아니어야 유효 신호로 본다.
    """
    if len(counts) < KAKAO_MIN_POINTS:
        return False
    return any(row["ratio"] > 0 for row in counts)


def _baseline_avg(counts: list[dict]) -> float:
    """
    baseline(최신일=today_value 제외)의 평균 ratio를 반환한다.
    표본 부족 판정(KAKAO_MIN_BASELINE_AVG)에 사용한다.
    """
    ordered = sorted(counts, key=lambda row: row["date"])
    baseline = ordered[:-1]  # 최신일은 today_value 이므로 baseline에서 제외
    if not baseline:
        return 0.0
    return sum(float(row["ratio"]) for row in baseline) / len(baseline)


if __name__ == "__main__":
    _keyword = input("키워드 입력: ").strip()
    result = get_meme_trend(_keyword)
    print(f"키워드:  {result['keyword']}")
    print(f"final_z: {result['final_z']:.4f}  ({result['ensemble_note']}, 소스: {', '.join(result['sources'])})")
    print(f"  naver_z: {result['naver_z']:.4f}")
    print(f"  kakao_z: {result['kakao_z']:.4f}  (blog_z: {result['blog_z']:.4f}, cafe_z: {result['cafe_z']:.4f})")
    print(f"상태:    {result['status']}")
    print(f"flags:   {result['flags'] or '없음'}")
    print(f"네이버 {len(result['ratios'])}건 / 카카오 {len(result['kakao_counts'])}일")

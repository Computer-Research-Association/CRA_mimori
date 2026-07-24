"""
trend_service.py
밈 이름 하나를 받아 유행 상태를 판정하는 상위 서비스 함수.

세 개의 독립 신호를 robust z-score 로 환산한 뒤 앙상블한다.
  - naver_z  : 네이버 데이터랩 검색량(국내 검색 수요)    → 주 지표
  - kakao_z  : 카카오 블로그/카페 언급량(콘텐츠 공급)    → 보조 지표
  - google_z : 구글 트렌드 검색 관심도(보조 수요 지표)   → 보조 지표

카카오는 채널을 분리해 blog_z / cafe_z 를 따로 계산한 뒤 합친다.
  kakao_z = KAKAO_BLOG_WEIGHT * blog_z + KAKAO_CAFE_WEIGHT * cafe_z
채널 분리로 'blog 중심 확산' vs '커뮤니티(cafe) 중심 유행' 해석이 가능해진다.

앙상블 가중치: naver 0.4 / kakao 0.3 / google 0.2 (활성 소스 가중치 합으로 정규화)
  - google 제외 시: naver 0.6 / kakao 0.4 (스펙 명시 폴백)
  - kakao 까지 제외 시: naver 단독
google(pytrends)은 비공식 라이브러리라 레이트리밋/차단이 잦다 → 실패·표본 부족 시
자동 제외하고 ensemble_note 에 사유를 남긴다. (가중치는 초기값, 실측 재보정 대상.)

크롤링 데이터(MongoDB)는 여기서 쓰지 않는다 - cold-start / 노이즈 문제로
트렌드 판정에서는 배제하고 RAG 근거 자료로만 활용하기로 결정.
"""

from itertools import combinations

from trend.datalab_client import DataLabClient
from trend.google_client import GoogleTrendsClient
from trend.kakao_client import KakaoSearchClient, merge_daily_series
from trend.zscore import classify_trend, drop_incomplete_today, zscore_from_series

# 앙상블 가중치(전 소스 활성 기준). 계산 시 활성 소스 가중치 합으로 정규화한다.
NAVER_WEIGHT = 0.4
KAKAO_WEIGHT = 0.3
GOOGLE_WEIGHT = 0.2

# google 제외 시 폴백 가중치.
# 비례 재정규화(0.4/0.7≈0.57, 0.3/0.7≈0.43) 대신 스펙에서 명시한 0.6/0.4 를 쓴다.
FALLBACK_NAVER_KAKAO = {"naver": 0.6, "kakao": 0.4}

# 카카오 채널별 가중치 (합 = 1.0). blog/cafe 를 kakao_z 로 합칠 때 사용.
KAKAO_BLOG_WEIGHT = 0.5
KAKAO_CAFE_WEIGHT = 0.5

# 보조 소스 시계열이 이보다 적으면 신뢰할 수 없어 해당 소스를 제외한다.
KAKAO_MIN_POINTS = 7

# baseline 평균 하한(저빈도 키워드의 z 불안정 방지). 카카오/구글 동일 패턴.
KAKAO_MIN_BASELINE_AVG = 10
GOOGLE_MIN_BASELINE_AVG = 10

# 앙상블에 반영된 소스들의 pairwise z 격차가 이 이상이고 부호가 반대면
# 발산 플래그를 남긴다. 초기값이며 실측 후 조정.
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
            "google_z": float,       # 구글 트렌드 z (제외 시에도 참고용으로 기록)
            "ensemble_note": str,    # ensemble / 제외 사유들을 "+" 로 연결
            "flags": list[str],      # 예: supply_demand_divergence
            "sources": list[str],    # 최종 판정에 실제 반영된 소스
            "ratios": list[dict],    # 네이버 데이터랩 시계열 (기존 호환)
            "kakao_counts": list[dict],   # blog+cafe 합산 시계열
            "blog_counts": list[dict],
            "cafe_counts": list[dict],
            "google_ratios": list[dict] | None,  # None = 요청 실패
        }
    """
    if related_keywords is None:
        related_keywords = [f"{keyword} 뜻", f"{keyword}가 뭐야"]

    note_parts: list[str] = []

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

    kakao_usable = kakao_active and kakao_baseline_avg >= KAKAO_MIN_BASELINE_AVG
    if not kakao_active:
        note_parts.append("kakao_excluded_no_signal")
    elif not kakao_usable:
        note_parts.append("kakao_excluded_low_baseline")

    # ── 구글 트렌드 (보조 지표) ───────────────────────────────────────────
    google_ratios = _safe_google_ratios(keyword)
    google_z = 0.0
    google_usable = False
    if google_ratios is None:
        # pytrends 요청 실패(레이트리밋/차단 등) → 남은 소스로 재정규화
        note_parts.append("google_unavailable")
    else:
        google_scored = drop_incomplete_today(google_ratios)
        google_active = _has_signal(google_scored)
        google_baseline_avg = _baseline_avg(google_scored) if google_active else 0.0
        google_z = zscore_from_series(google_scored) if google_active else 0.0
        if not google_active:
            note_parts.append("google_excluded_no_signal")
        elif google_baseline_avg < GOOGLE_MIN_BASELINE_AVG:
            note_parts.append("google_excluded_low_baseline")
        else:
            google_usable = True

    # ── 앙상블 (활성 소스 가중치 합으로 정규화) ───────────────────────────
    weights = {"naver": NAVER_WEIGHT}
    zs = {"naver": naver_z}
    if kakao_usable:
        weights["kakao"] = KAKAO_WEIGHT
        zs["kakao"] = kakao_z
    if google_usable:
        weights["google"] = GOOGLE_WEIGHT
        zs["google"] = google_z

    if set(weights) == {"naver", "kakao"}:
        weights = dict(FALLBACK_NAVER_KAKAO)  # 스펙 명시 폴백(0.6/0.4)

    total_w = sum(weights.values())
    final_z = sum(weights[s] * zs[s] for s in weights) / total_w
    sources = [s for s in ("naver", "kakao", "google") if s in weights]
    ensemble_note = "ensemble" if not note_parts else "+".join(note_parts)

    # ── 수요/공급 발산 플래그 (활성 소스 pairwise) ────────────────────────
    # 카카오는 합산 평균 kakao_z 대신 '지배 채널' z로 비교한다 - 한 채널만
    # 폭발한 도배 패턴이 평균 희석에 묻히지 않게 하기 위함(기존 로직 유지).
    # 게이트를 통과해 앙상블에 실제 반영된 소스들만 비교한다
    # (저baseline 소스의 불안정한 z로 플래그가 오발되는 것을 방지).
    dominant_channel_z = blog_z if abs(blog_z) >= abs(cafe_z) else cafe_z
    div_values = {"naver": naver_z}
    if kakao_usable:
        div_values["kakao"] = dominant_channel_z
    if google_usable:
        div_values["google"] = google_z

    flags: list[str] = []
    for a, b in combinations(div_values, 2):
        va, vb = div_values[a], div_values[b]
        if abs(va - vb) > DIVERGENCE_THRESHOLD and va * vb < 0:
            flags.append("supply_demand_divergence")
            break  # 한 쌍이라도 발산이면 충분

    return {
        "keyword": keyword,
        "z_score": final_z,  # 기존 호환 별칭
        "final_z": final_z,
        "status": classify_trend(final_z),
        "naver_z": naver_z,
        "kakao_z": kakao_z,
        "blog_z": blog_z,
        "cafe_z": cafe_z,
        "google_z": google_z,
        "ensemble_note": ensemble_note,
        "flags": flags,
        "sources": sources,
        "ratios": naver_ratios,
        "kakao_counts": kakao_counts,
        "blog_counts": blog_counts,
        "cafe_counts": cafe_counts,
        "google_ratios": google_ratios,
    }


def _safe_kakao_channels(keyword: str) -> dict:
    """
    카카오 채널별 시계열을 수집하되, 키 미설정/네트워크 오류 시 빈 채널을 반환한다.
    보조 지표가 실패해도 네이버 단독 판정은 계속되어야 하므로 예외를 삼킨다.
    """
    try:
        return KakaoSearchClient().get_channel_counts(keyword)
    except Exception as exc:  # noqa: BLE001 - 보조 지표 실패는 치명적이지 않음
        print(f"[trend_service] 카카오 지표 수집 실패, 제외 진행: {exc}")
        return {"blog": [], "cafe": []}


def _safe_google_ratios(keyword: str) -> list[dict] | None:
    """
    구글 트렌드 시계열을 수집한다. 클라이언트가 자체적으로 재시도/캐시를 처리하며
    실패 시 None 을 반환한다. 임포트 오류 등 예외도 None 으로 흡수한다.
    """
    try:
        return GoogleTrendsClient().get_recent_ratios(keyword)
    except Exception as exc:  # noqa: BLE001 - 보조 지표 실패는 치명적이지 않음
        print(f"[trend_service] 구글 지표 수집 실패, 제외 진행: {exc}")
        return None


def _has_signal(counts: list[dict]) -> bool:
    """
    보조 소스 시계열이 앙상블에 쓸 만한지 판단한다(카카오/구글 공용).
    최소 포인트 수를 채우고, 값이 전부 0이 아니어야 유효 신호로 본다.
    """
    if len(counts) < KAKAO_MIN_POINTS:
        return False
    return any(row["ratio"] > 0 for row in counts)


def _baseline_avg(counts: list[dict]) -> float:
    """
    baseline(최신일=today_value 제외)의 평균 ratio를 반환한다.
    표본 부족 판정(*_MIN_BASELINE_AVG)에 사용한다.
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
    print(f"  naver_z:  {result['naver_z']:.4f}")
    print(f"  kakao_z:  {result['kakao_z']:.4f}  (blog_z: {result['blog_z']:.4f}, cafe_z: {result['cafe_z']:.4f})")
    print(f"  google_z: {result['google_z']:.4f}")
    print(f"상태:    {result['status']}")
    print(f"flags:   {result['flags'] or '없음'}")
    google_len = len(result['google_ratios']) if result['google_ratios'] else 0
    print(f"네이버 {len(result['ratios'])}건 / 카카오 {len(result['kakao_counts'])}일 / 구글 {google_len}건")

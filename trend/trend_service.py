"""
trend_service.py
밈 이름 하나를 받아 유행 상태를 판정하는 상위 서비스 함수.

세 개의 독립 신호를 robust z-score 로 환산한 뒤 앙상블한다.
  - naver_z  : 네이버 데이터랩 검색량(국내 검색 수요)    → 주 지표
  - kakao_z  : 카카오 블로그/카페 언급량(콘텐츠 공급)    → 보조 지표
  - google_z : 구글 트렌드 검색 관심도(보조 수요 지표)   → 보조 지표

카카오는 채널을 분리해 blog_z / cafe_z 를 따로 계산한 뒤 합친다.
  kakao_z = (활성 채널 가중치 합으로 정규화한) blog_z / cafe_z 가중 평균
채널 분리로 'blog 중심 확산' vs '커뮤니티(cafe) 중심 유행' 해석이 가능해진다.
무신호 채널(전부 0)은 정규화에서 빠져 남은 채널을 희석하지 않는다.

앙상블 가중치: naver 0.4 / kakao 0.3 / google 0.2 (활성 소스 가중치 합으로 정규화)
  - google 제외 시: naver 0.6 / kakao 0.4 (스펙 명시 폴백)
  - kakao 까지 제외 시: naver 단독
  - naver 무신호(API 실패/키워드 없음)면 보조 소스(kakao/google)가 살아 있어도
    판정하지 않는다 → status="데이터 부족". 네이버는 주 지표라 없으면 판정을 보류한다
    (보조 소스 단독 판정은 신호가 약해 오판 위험이 크다).
    (무신호 z=0.0 은 중립 밴드로 "평상"이 되지만, '진짜 평상'과 구분하려 판정을 보류)
google(pytrends)은 비공식 라이브러리라 레이트리밋/차단이 잦다 → 실패·표본 부족 시
자동 제외하고 ensemble_note 에 사유를 남긴다. (가중치는 초기값, 실측 재보정 대상.)

크롤링 데이터(MongoDB)는 여기서 쓰지 않는다 - cold-start / 노이즈 문제로
트렌드 판정에서는 배제하고 RAG 근거 자료로만 활용하기로 결정.
"""

from datetime import date, datetime, timezone
from itertools import combinations

from trend.datalab_client import DataLabClient
from trend.google_client import GoogleTrendsClient
from trend.kakao_client import KakaoSearchClient, merge_daily_series
from trend.zscore import (
    STATUS_INSUFFICIENT,
    classify_trend,
    drop_incomplete_today,
    zscore_from_series,
)

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

# 시계열이 이 포인트 수보다 적으면 신뢰할 수 없어 해당 소스를 게이트에서 제외한다.
# 네이버/카카오/구글 세 소스의 _has_signal 게이트가 공용으로 쓴다.
MIN_SERIES_POINTS = 7

# 소스별 IQR 바닥값(robust z 분모 하한).
# robust scaling 은 IQR 이 분모로 실제 쓰이는 구간에서만 스케일 불변이다.
# 저빈도 키워드처럼 바닥값이 걸리는 구간에서는 분모가 상수로 고정되므로,
# 소스의 값 스케일에 맞춘 서로 다른 바닥값이 필요하다.
#   - 네이버/구글: 0~100 상대 검색량 → 2.0
#   - 카카오: 언급 '건수'(상대값보다 스케일이 크고 분산도 큼) → 5.0
# 모두 초기값이며 실측 후 재보정 대상. (기존엔 셋 다 zscore.MIN_IQR=2.0 을 공유해
# 카카오 저빈도 구간의 z 스케일이 검색량 소스와 어긋나던 문제를 분리했다.)
NAVER_MIN_IQR = 2.0
GOOGLE_MIN_IQR = 2.0
KAKAO_MIN_IQR = 5.0

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
            "status": str,           # 핫함 / 유행 중 / 평상 / 감소 / 소멸 / 데이터 부족
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
    naver_ratios = _safe_naver_ratios(keyword, related_keywords)
    # 미완성 당일은 판정에서 제외(완성된 최근일을 today_value로)
    naver_scored = drop_incomplete_today(naver_ratios)
    naver_z = zscore_from_series(naver_scored, min_iqr=NAVER_MIN_IQR)
    # 주 지표도 무신호(API 실패/키워드 없음)면 게이트로 제외한다.
    # 이 경우 naver_z=0.0 이 그대로 앙상블에 들어가 "유행 중"으로 둔갑하는 걸 막는다.
    naver_usable = _has_signal(naver_scored)
    if not naver_usable:
        note_parts.append("naver_excluded_no_signal")

    # ── 카카오 블로그/카페 (보조 지표, 채널 분리) ─────────────────────────
    channels = _safe_kakao_channels(keyword)
    blog_counts, cafe_counts = channels["blog"], channels["cafe"]
    # 합산 시계열: baseline 게이트 / 신호 유무는 '합산 기준'으로 판단.
    # merge_daily_series 는 두 채널의 '공통(교집합) 날짜'만 합산한다(한 채널만
    # 500건 포화로 최근 구간만 남는 경우 대비). 채널별 z 도 반드시 이 공통 구간에서
    # 계산해야 합산 z 와 같은 창(window)을 보게 되고, 공통 밖 과거로 채널이 '활성'
    # 처럼 보이거나 발산 플래그가 어긋나는 일이 없다. → 채널 시계열도 공통일로 제한.
    kakao_counts = merge_daily_series(blog_counts, cafe_counts)
    common_dates = {row["date"] for row in kakao_counts}
    # 스코어링용 공통 구간 시계열(원본 blog_counts/cafe_counts 는 기록·시각화용으로 보존).
    blog_common = [row for row in blog_counts if row["date"] in common_dates]
    cafe_common = [row for row in cafe_counts if row["date"] in common_dates]

    kakao_scored = drop_incomplete_today(kakao_counts)
    kakao_active = _has_signal(kakao_scored)
    kakao_baseline_avg = _baseline_avg(kakao_scored) if kakao_active else 0.0

    # 채널별 z (합산이 활성일 때만 계산; 아니면 0.0).
    # 가중치는 '실제로 신호가 있는' 채널들의 합으로 정규화한다. 이렇게 안 하면
    # blog 에만 언급이 몰리고 cafe 는 전부 0인 밈에서 cafe_z=0 이 절반 가중으로
    # 섞여 kakao_z 가 blog_z 의 절반으로 눌린다(무신호 채널에 의한 희석).
    # → 한 채널만 활성이면 그 채널이 kakao_z 를 그대로 대표한다.
    if kakao_active:
        blog_scored = drop_incomplete_today(blog_common)
        cafe_scored = drop_incomplete_today(cafe_common)
        blog_ch_active = _has_signal(blog_scored)
        cafe_ch_active = _has_signal(cafe_scored)

        blog_z = (
            zscore_from_series(blog_scored, min_iqr=KAKAO_MIN_IQR)
            if blog_ch_active
            else 0.0
        )
        cafe_z = (
            zscore_from_series(cafe_scored, min_iqr=KAKAO_MIN_IQR)
            if cafe_ch_active
            else 0.0
        )

        ch_weights: dict[str, float] = {}
        if blog_ch_active:
            ch_weights["blog"] = KAKAO_BLOG_WEIGHT
        if cafe_ch_active:
            ch_weights["cafe"] = KAKAO_CAFE_WEIGHT
        # kakao_active(합산 시계열에 신호 있음)면 최소 한 채널은 활성이라 분모>0 보장.
        total_ch_w = sum(ch_weights.values())
        kakao_z = (
            ch_weights.get("blog", 0.0) * blog_z
            + ch_weights.get("cafe", 0.0) * cafe_z
        ) / total_ch_w
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
        google_z = (
            zscore_from_series(google_scored, min_iqr=GOOGLE_MIN_IQR)
            if google_active
            else 0.0
        )
        if not google_active:
            note_parts.append("google_excluded_no_signal")
        elif google_baseline_avg < GOOGLE_MIN_BASELINE_AVG:
            note_parts.append("google_excluded_low_baseline")
        else:
            google_usable = True

    # ── 앙상블 (네이버=주 지표 필수, 보조 소스는 활성 시에만 가중 합산) ─────
    # 주 지표(네이버)가 무신호면 보조 지표(카카오/구글)가 아무리 살아있어도
    # 판정하지 않는다. 보조 지표는 단독으로 신뢰하기엔 신호가 약해(콘텐츠 공급량·
    # 비공식 API) 오판 위험이 크므로, 네이버 없이는 '데이터 부족'으로 판정을 보류한다.
    if not naver_usable:
        final_z = 0.0
        status = STATUS_INSUFFICIENT
        sources: list[str] = []
    else:
        weights: dict[str, float] = {"naver": NAVER_WEIGHT}
        zs: dict[str, float] = {"naver": naver_z}
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
        status = classify_trend(final_z)
        sources = [s for s in ("naver", "kakao", "google") if s in weights]

    ensemble_note = "ensemble" if not note_parts else "+".join(note_parts)

    # ── 수요/공급 발산 플래그 (활성 소스 pairwise) ────────────────────────
    # 카카오는 합산 평균 kakao_z 대신 '지배 채널' z로 비교한다 - 한 채널만
    # 폭발한 도배 패턴이 평균 희석에 묻히지 않게 하기 위함(기존 로직 유지).
    # 앙상블에 실제 반영된 소스(sources)만 비교한다. 저baseline 소스의 불안정한
    # z로 플래그가 오발되는 것을 막고, 네이버 무신호로 판정을 보류한 경우
    # (sources 비어 있음)에는 보조 소스끼리 발산 플래그가 새지 않게 한다.
    dominant_channel_z = blog_z if abs(blog_z) >= abs(cafe_z) else cafe_z
    div_values: dict[str, float] = {}
    if "naver" in sources:
        div_values["naver"] = naver_z
    if "kakao" in sources:
        div_values["kakao"] = dominant_channel_z
    if "google" in sources:
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
        "status": status,
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


def format_trend_context(keyword: str, result: dict | None = None) -> str:
    """
    get_meme_trend() 결과를 LLM 프롬프트에 넣기 좋은 짧은 문자열로 요약한다.
    (analysis/pipeline.py, analysis/rag_pipeline.py 가 프롬프트 조립 시 사용)

    상태가 STATUS_INSUFFICIENT("데이터 부족")이거나 조회 중 예외가 나면 ""을 반환한다.
    ""으로 통일하는 이유: 트렌드는 어디까지나 보조 지표라, 판정할 근거가 없을 때
    "데이터 부족" 문구를 그대로 보여주는 것보다 그냥 안 보여주는 편이 프롬프트를
    깔끔하게 유지한다(LLM이 그 문구 자체를 유행 근거로 오인할 여지도 없앤다).
    """
    if result is None:
        try:
            result = get_meme_trend(keyword)
        except Exception as exc:  # noqa: BLE001 - 트렌드 조회 실패가 분석/RAG 자체를 막으면 안 됨
            print(f"[trend_service] format_trend_context 실패, 빈 문자열 반환: {exc}")
            return ""

    if result["status"] == STATUS_INSUFFICIENT:
        return ""

    sources_str = "+".join(result["sources"]) if result["sources"] else "없음"
    flag_str = f" ({', '.join(result['flags'])})" if result["flags"] else ""

    return (
        "[참고: 최근 검색/언급량 기반 유행 상태 앙상블 판정 — 정성적 분석의 보조 지표로만 활용]\n"
        "z-score 기준: z>2=핫함 / z≥0.5=유행 중 / |z|<0.5=평상 / z≥-2=감소 / z<-2=소멸\n"
        f"상태: {result['status']} (robust z-score: {result['final_z']:.2f}, 반영 소스: {sources_str}){flag_str}"
    )


def _format_trend_console(result: dict) -> str:
    """get_meme_trend 결과를 콘솔 표시용 상세 문자열로 변환한다(소스별 z 포함).

    format_trend_context가 앙상블 final_z 하나만 보여주는 것과 달리, 판정에 실제
    반영된 소스(result["sources"])의 개별 z를 함께 노출한다. 반영되지 않은 소스는
    '미반영'으로 표기해 어떤 신호가 판정에 들어갔는지 한눈에 보이게 한다.
    """
    if result["status"] == STATUS_INSUFFICIENT:
        return "판정 불가 (데이터 부족 — 네이버 주지표 무신호)"

    z_by_source = {
        "naver": result["naver_z"],
        "kakao": result["kakao_z"],
        "google": result["google_z"],
    }
    parts = [
        f"{s}={z_by_source[s]:+.2f}" if s in result["sources"] else f"{s}=미반영"
        for s in ("naver", "kakao", "google")
    ]
    flag_str = f" | flags: {', '.join(result['flags'])}" if result["flags"] else ""
    return (
        f"상태: {result['status']} | final_z={result['final_z']:+.2f} "
        f"({', '.join(parts)}){flag_str}"
    )


def format_trend_for_rag(keyword: str) -> tuple[str, str]:
    """RAG 실행용: get_meme_trend를 1회만 호출해
    (콘솔 표시용 상세 문자열, 프롬프트 주입용 요약 문자열)을 함께 반환한다.

    두 문자열이 같은 조회 결과를 공유하므로 네트워크 중복 호출이 없다.
    조회 실패 시 ("판정 불가 (수집 실패)", "")를 반환해 RAG 흐름을 막지 않는다.
    """
    try:
        result = get_meme_trend(keyword)
    except Exception as exc:  # noqa: BLE001 - 트렌드 조회 실패가 RAG 자체를 막으면 안 됨
        print(f"[trend_service] 트렌드 조회 실패, 판정 생략: {exc}")
        return "판정 불가 (수집 실패)", ""
    return _format_trend_console(result), format_trend_context(keyword, result=result)


def save_trend_score(result: dict) -> None:
    """
    get_meme_trend 결과를 trend_scores 컬렉션에 저장한다.

    같은 키워드라도 날짜별로 추이를 남겨야 하므로 (keyword, date) 를 키로 upsert 한다.
    → 하루에 여러 번 실행하면 그날 문서는 덮어써지고, 날짜가 바뀌면 새 문서가 쌓인다.

    판정 로직(get_meme_trend)은 DB 없이도 동작해야 하므로 DB/config 임포트는
    이 함수 안에서만 지연 임포트한다(trend_service 를 순수 계산용으로 단독 실행 가능).
    """
    from DB.mongo_client import get_collection
    from config.config_cilent import TREND_COLLECTION

    today = date.today().isoformat()
    doc = {**result, "date": today, "evaluated_at": datetime.now(timezone.utc)}
    get_collection(TREND_COLLECTION).update_one(
        {"keyword": result["keyword"], "date": today},
        {"$set": doc},
        upsert=True,
    )


def _safe_naver_ratios(keyword: str, related_keywords: list[str]) -> list[dict]:
    """
    네이버 데이터랩 시계열을 수집하되, 네트워크/키 오류 시 빈 리스트를 반환한다.
    주 지표가 실패하면 빈 시계열 → 무신호로 게이트에서 제외되고 상태는 '데이터 부족'이 된다.
    """
    try:
        return DataLabClient().get_recent_ratios(
            keyword, related_keywords=related_keywords
        )
    except Exception as exc:  # noqa: BLE001 - 실패를 판정 불가로 흡수(크래시 방지)
        print(f"[trend_service] 네이버 지표 수집 실패, 제외 진행: {exc}")
        return []


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
    시계열이 앙상블에 쓸 만한지 판단한다(네이버/카카오/구글 공용 게이트).
    최소 포인트 수(MIN_SERIES_POINTS)를 채우고, 값이 전부 0이 아니어야 유효 신호로 본다.
    """
    if len(counts) < MIN_SERIES_POINTS:
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

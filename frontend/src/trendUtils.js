// trend/zscore.py classify_trend()가 반환하는 실제 상태 문자열과 매핑한 표시용 이모지.
const STATUS_EMOJI = {
  핫함: '🔥',
  '유행 중': '📈',
  평상: '📊',
  감소: '📉',
  소멸: '💤',
}

const SOURCE_LABELS = {
  naver: '네이버',
  kakao: '카카오',
  google: '구글',
}

export function statusEmoji(status) {
  return STATUS_EMOJI[status] ?? '🔍'
}

// trend/zscore.py classify_trend()의 5개 실제 상태를 색상 키로 매핑.
// "데이터 부족"은 순위 밖(집계 전) 별도 그룹이라 여기 포함하지 않는다.
// 티어 칩(tier-pill--*)과 트렌드 차트 선(trend-chart__svg--*) 둘 다 같은 키를
// 써서 "이 색 = 이 상태"가 화면 어디서든 같은 뜻이 되도록 한다.
const TIER_COLOR_KEY = {
  핫함: 'hot',
  '유행 중': 'up',
  평상: 'flat',
  감소: 'down',
  소멸: 'extinct',
}

export function tierColorKey(status) {
  return TIER_COLOR_KEY[status] ?? 'flat'
}

export function tierPillClass(status) {
  return `tier-pill--${tierColorKey(status)}`
}

// "Electric fusion" 4색 그라디언트 — 시안(감소/최저값) → 그린 → 오렌지 → 레드(증가/
// 최고값). 티어 칩의 5단계 색과 같은 스펙트럼을 그대로 쓰되, 여기서는 차트 한 장 안의
// 실제 값 범위(min~max)를 0~1로 정규화해 연속적으로 칠한다 — "언급량이 변하는 만큼
// 색도 단계 없이 부드럽게 변한다"는 요청이라 5단계로 끊지 않고 원본 그라디언트를 그대로 샘플링.
const VALUE_GRADIENT_STOPS = [
  { t: 0, rgb: [0, 240, 255] },   // #00F0FF 시안
  { t: 1 / 3, rgb: [0, 255, 129] },   // #00FF81 그린
  { t: 2 / 3, rgb: [255, 169, 0] },   // #FFA900 오렌지
  { t: 1, rgb: [255, 104, 0] },   // #FF6800 레드
]

export function valueGradientColor(t) {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0.5))
  for (let i = 0; i < VALUE_GRADIENT_STOPS.length - 1; i++) {
    const a = VALUE_GRADIENT_STOPS[i]
    const b = VALUE_GRADIENT_STOPS[i + 1]
    if (clamped >= a.t && clamped <= b.t) {
      const localT = (clamped - a.t) / (b.t - a.t)
      const [r, g, b2] = a.rgb.map((c, idx) => Math.round(c + (b.rgb[idx] - c) * localT))
      return `rgb(${r}, ${g}, ${b2})`
    }
  }
  const last = VALUE_GRADIENT_STOPS[VALUE_GRADIENT_STOPS.length - 1].rgb
  return `rgb(${last[0]}, ${last[1]}, ${last[2]})`
}

export function sourceLabel(source) {
  return SOURCE_LABELS[source] ?? source
}

// AnalysisPanel의 트렌드 표시와 동일한 기준(데이터 부족이면 숨김, z가 숫자가 아니면 숨김).
export function usableZ(trend) {
  if (!trend || trend.status === '데이터 부족') return null
  const z = trend.final_z ?? trend.z_score
  return typeof z === 'number' ? z : null
}

// trend_service.get_meme_trend()가 반환하는 시계열 중 실제로 판정에 쓸 만큼(2포인트 이상)
// 있는 첫 번째 시리즈를 고른다. naver(주 지표) → kakao → google 순.
export function pickPrimarySeries(trend) {
  if (!trend) return null
  const candidates = [
    { points: trend.ratios, label: '네이버 검색 관심도' },
    { points: trend.kakao_counts, label: '카카오 블로그·카페 언급량' },
    { points: trend.google_ratios, label: '구글 트렌드 검색 관심도' },
  ]
  for (const candidate of candidates) {
    if (Array.isArray(candidate.points) && candidate.points.length >= 2) return candidate
  }
  return null
}

// 최신값이 그 이전 baseline 평균 대비 몇 % 변화했는지. 백엔드가 주는 필드가 아니라
// 이미 받은 실제 시계열(ratios 등)에서 프론트가 계산하는 파생값이다.
export function computeChangeRate(points) {
  if (!Array.isArray(points) || points.length < 2) return null
  const sorted = [...points].sort((a, b) => (a.date < b.date ? -1 : 1))
  const latest = Number(sorted[sorted.length - 1].ratio)
  const baseline = sorted.slice(0, -1)
  const baselineAvg = baseline.reduce((sum, row) => sum + Number(row.ratio || 0), 0) / baseline.length
  if (!Number.isFinite(latest) || !(baselineAvg > 0)) return null
  return ((latest - baselineAvg) / baselineAvg) * 100
}

// changeRate(시계열 파생, %) 를 우선 쓰고 없으면 z-score 부호로 대체한다.
// 둘 다 실제로 받은 데이터에서 나온 값이라 "임의 생성"이 아니다.
export function trendDirectionLabel({ changeRate, z }) {
  if (typeof changeRate === 'number') {
    if (changeRate > 5) return '평균 대비 상승세'
    if (changeRate < -5) return '평균 대비 하락세'
    return '평균과 비슷한 수준'
  }
  if (typeof z === 'number') {
    if (z > 0.5) return '평균 대비 상승세'
    if (z < -0.5) return '평균 대비 하락세'
    return '평균과 비슷한 수준'
  }
  return null
}

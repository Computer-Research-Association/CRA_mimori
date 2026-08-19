import { useEffect, useState } from 'react'
import { valueGradientColor } from '../trendUtils.js'

const WIDTH = 320
const HEIGHT = 180 // 기준선 4개 + 평균선이 한 화면에 들어가려면 세로 여유가 더 필요하다
const PAD_X = 4
const PAD_TOP = 36 // 꺾이는 지점 날짜 라벨이 들어갈 여유
const PAD_BOTTOM = 16
const GRADIENT_ID = 'trend-chart-gradient'

// 이웃보다 최소 이만큼(전체 스팬 대비 비율) 튀어야 "꺾이는 지점"으로 표시한다.
// 안 그러면 매일의 잡음(±1~2)까지 전부 라벨이 붙어 30일 치가 날짜로 뒤덮인다.
const MIN_PROMINENCE_RATIO = 0.15
const MAX_LABELED_POINTS = 6

// trend/zscore.py classify_trend()과 같은 경계(z=2/0.5/-0.5/-2)를 이 차트에 찍힌
// 값들만으로 근사한다 — 실제 판정에 쓰인 baseline 평균/표준편차는 백엔드 내부 값이라
// 프론트에 안 내려오므로, 여기서는 "이 30일 창의 평균/표준편차" 기준으로 같은 공식
// (mean + z*std)을 적용한다. 실제 트렌드 판정 문턱과 다를 수 있는 근사치라는 점을
// 라벨/캡션에 명시한다.
const TIER_THRESHOLDS = [
  { z: 2, label: '핫함' },
  { z: 0.5, label: '유행 중' },
  { z: -0.5, label: '평상' },
  { z: -2, label: '감소' },
]

function computeTierLines(values, mean, min, max) {
  const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / values.length
  const std = Math.sqrt(variance)
  if (!(std > 0)) return []
  return TIER_THRESHOLDS.map(({ z, label }) => ({ label, value: mean + z * std }))
    .filter((line) => line.value >= min && line.value <= max)
}

function formatShortDate(iso) {
  const parts = iso.split('-')
  if (parts.length !== 3) return iso
  return `${parts[1]}/${parts[2]}`
}

// 국소 최댓값(관심이 튄 시점) 중 "충분히 튀는" 지점만 골라 날짜 라벨을 붙인다.
// 밸리(국소 최솟값)는 뺐다 — 스파이크 바로 다음 칸은 급락이라 밸리로도 잡혀서
// 피크 라벨 바로 옆에 중복 라벨이 붙는 문제가 있었고, 애초에 "관심도가 언제
// 튀었는지"가 궁금한 정보지 "언제 가라앉았는지"는 부가 정보라 우선순위가 낮다.
// 값 하나짜리 뾰족한 잡음은 걸러지도록 이웃 대비 돌출 정도(prominence)로 걸러내고,
// 그래도 많으면(노이즈가 큰 시계열) 돌출이 큰 순으로 상한을 둔다.
function findTurningPoints(values, span) {
  const minProminence = span * MIN_PROMINENCE_RATIO
  const candidates = []
  for (let i = 1; i < values.length - 1; i++) {
    const prev = values[i - 1]
    const cur = values[i]
    const next = values[i + 1]
    const isPeak = cur > prev && cur > next
    if (!isPeak) continue
    const prominence = cur - Math.min(prev, next)
    if (prominence >= minProminence) {
      candidates.push({ index: i, prominence })
    }
  }
  candidates.sort((a, b) => b.prominence - a.prominence)
  return candidates
    .slice(0, MAX_LABELED_POINTS)
    .map((c) => c.index)
    .sort((a, b) => a - b)
}

// 새 차트 라이브러리 없이 실제 시계열(points)만으로 그리는 가벼운 SVG 라인 차트.
// 평균선은 이 차트에 찍힌 30일 관심도(ratio)의 평균이다 — 분석 문구에 같이 나오는
// z-score는 별도 앙상블 계산값이라 서로 다른 기준이므로 섞어 부르지 않는다.
//
// 선 색은 "언급량이 낮다↔높다"를 그대로 색으로 인코딩한다 — 이 차트에 찍힌 값의
// min~max를 0~1로 정규화해 electric fusion 그라디언트(시안=최저 → 레드=최고)에서
// 뽑는다. 외부에서 받은 트렌드 상태(핫함/평상 등)가 아니라 이 차트 자체의 실제
// 값 변화를 반영하는 것이라 더 정직하다 — 값이 오르내리는 대로 선 색도 따라간다.
export default function TrendChart({ points, label }) {
  // 키워드를 바꿔가며 봐도(=points가 바뀔 때마다) 매번 다시 그려지는 느낌을 주려고
  // mount 여부가 아니라 points 자체를 의존성으로 둔다. 두 번째 렌더에서 클래스를
  // 붙여야 브라우저가 시작 상태(dashoffset:1)를 먼저 페인트하고 나서 전환하므로,
  // 같은 렌더에서 바로 붙이면 전환 없이 완성된 모양으로 튀어 보인다.
  const [drawn, setDrawn] = useState(false)
  useEffect(() => {
    setDrawn(false)
    const raf = requestAnimationFrame(() => setDrawn(true))
    return () => cancelAnimationFrame(raf)
  }, [points])

  if (!Array.isArray(points) || points.length < 2) return null

  const sorted = [...points].sort((a, b) => (a.date < b.date ? -1 : 1))
  const values = sorted.map((p) => Number(p.ratio) || 0)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const mean = values.reduce((sum, v) => sum + v, 0) / values.length

  const toY = (value) => HEIGHT - PAD_BOTTOM - ((value - min) / span) * (HEIGHT - PAD_TOP - PAD_BOTTOM)
  const toX = (i) => PAD_X + (i / (sorted.length - 1)) * (WIDTH - PAD_X * 2)
  const toT = (value) => (value - min) / span

  const coords = sorted.map((p, i) => [toX(i), toY(Number(p.ratio))])

  const linePath = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [lastX] = coords[coords.length - 1]
  const [firstX] = coords[0]
  const areaPath = `${linePath} L${lastX.toFixed(1)},${HEIGHT} L${firstX.toFixed(1)},${HEIGHT} Z`
  const meanY = toY(mean)

  const first = sorted[0]
  const last = sorted[sorted.length - 1]
  const turningPoints = findTurningPoints(values, span)
  const tierLines = computeTierLines(values, mean, min, max)

  return (
    <div className="trend-chart">
      <p className="trend-chart__label">
        최근 {sorted.length}일 {label} 추이 · 굵은 점선은 기간 평균, 가는 점선은 핫함/유행 중/평상/감소 기준선(이 구간 값 기준 근사치) · 선 색은 값의 높낮이(파랑=낮음, 빨강=높음)
      </p>
      <div className="trend-chart__plot">
        {/* 기준선 라벨(핫함/유행 중/...)은 SVG 옆의 별도 칸에 그린다 — 라벨이 SVG
            내부(=실제 데이터가 지나가는 영역)에 겹치면 지그재그 선과 뒤엉켜 침범한
            것처럼 보인다. 옆 칸으로 분리하면 기준선 자체는 여전히 데이터 위를
            지나가지만(참조선이니 당연함), 텍스트만큼은 데이터 영역 밖에 있다. */}
        <div className="trend-chart__svg-wrap">
          <svg
            className={`trend-chart__svg${drawn ? ' trend-chart__svg--drawn' : ''}`}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={`${label} 시계열 그래프, ${first.date}부터 ${last.date}까지. 굵은 점선은 이 기간 평균값, 가는 점선은 핫함/유행 중/평상/감소 기준선(근사치). 선 색은 값이 낮을수록 파랑, 높을수록 빨강. 꺾이는 지점 ${turningPoints.length}곳에 날짜 표시.`}
          >
            <defs>
              {/* userSpaceOnUse + y좌표만 다르게 줘서 값이 아니라 화면상 높낮이 기준
                  수직 그라디언트로 만든다 — toY가 값↑일수록 y를 작게 주므로
                  y1=최저값 위치(파랑) → y2=최고값 위치(빨강). */}
              <linearGradient id={GRADIENT_ID} gradientUnits="userSpaceOnUse" x1="0" y1={toY(min).toFixed(1)} x2="0" y2={toY(max).toFixed(1)}>
                <stop offset="0%" stopColor={valueGradientColor(0)} />
                <stop offset="33.33%" stopColor={valueGradientColor(1 / 3)} />
                <stop offset="66.67%" stopColor={valueGradientColor(2 / 3)} />
                <stop offset="100%" stopColor={valueGradientColor(1)} />
              </linearGradient>
            </defs>
            <path d={areaPath} className="trend-chart__area" />
            {tierLines.map(({ label: tierLabel, value }) => (
              <line
                key={tierLabel}
                className="trend-chart__tier-line"
                x1={PAD_X}
                x2={WIDTH - PAD_X}
                y1={toY(value).toFixed(1)}
                y2={toY(value).toFixed(1)}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <line
              className="trend-chart__mean"
              x1={PAD_X}
              x2={WIDTH - PAD_X}
              y1={meanY.toFixed(1)}
              y2={meanY.toFixed(1)}
              vectorEffect="non-scaling-stroke"
            />
            <path
              d={linePath}
              className="trend-chart__line"
              stroke={`url(#${GRADIENT_ID})`}
              vectorEffect="non-scaling-stroke"
              pathLength="1"
            />
          </svg>
          {/* 점 마커와 날짜 라벨은 SVG 밖 HTML로 그린다 — viewBox를 preserveAspectRatio=
              "none"으로 비균등 확대하는 중이라, SVG 안에 그리면 원이 타원으로, 글자가
              찌그러진 모양으로 나온다. %기반 절대 위치는 실제 렌더 크기를 그대로
              따라가므로 진짜 원/정상 텍스트로 보인다. */}
          {turningPoints.map((i) => (
            <span
              key={sorted[i].date}
              className="trend-chart__point"
              style={{
                left: `${(coords[i][0] / WIDTH) * 100}%`,
                top: `${(coords[i][1] / HEIGHT) * 100}%`,
                backgroundColor: valueGradientColor(toT(values[i])),
              }}
            />
          ))}
          {turningPoints.map((i) => (
            <span
              key={`${sorted[i].date}-label`}
              className="trend-chart__point-label"
              style={{ left: `${(coords[i][0] / WIDTH) * 100}%`, top: `${(coords[i][1] / HEIGHT) * 100}%` }}
            >
              {formatShortDate(sorted[i].date)}
            </span>
          ))}
        </div>
        <div className="trend-chart__tier-labels">
          {tierLines.map(({ label: tierLabel, value }) => (
            <span
              key={tierLabel}
              className="trend-chart__tier-label"
              style={{ top: `${(toY(value) / HEIGHT) * 100}%` }}
            >
              {tierLabel}
            </span>
          ))}
        </div>
      </div>
      <div className="trend-chart__axis">
        <span>{formatShortDate(first.date)}</span>
        <span>{formatShortDate(last.date)}</span>
      </div>
    </div>
  )
}

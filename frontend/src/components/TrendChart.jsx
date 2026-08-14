const WIDTH = 320
const HEIGHT = 88
const PAD_X = 4
const PAD_Y = 10

function formatShortDate(iso) {
  const parts = iso.split('-')
  if (parts.length !== 3) return iso
  return `${parts[1]}/${parts[2]}`
}

// 새 차트 라이브러리 없이 실제 시계열(points)만으로 그리는 가벼운 SVG 라인 차트.
export default function TrendChart({ points, label }) {
  if (!Array.isArray(points) || points.length < 2) return null

  const sorted = [...points].sort((a, b) => (a.date < b.date ? -1 : 1))
  const values = sorted.map((p) => Number(p.ratio) || 0)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1

  const coords = sorted.map((p, i) => {
    const x = PAD_X + (i / (sorted.length - 1)) * (WIDTH - PAD_X * 2)
    const y = HEIGHT - PAD_Y - ((Number(p.ratio) - min) / span) * (HEIGHT - PAD_Y * 2)
    return [x, y]
  })

  const linePath = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [lastX] = coords[coords.length - 1]
  const [firstX] = coords[0]
  const areaPath = `${linePath} L${lastX.toFixed(1)},${HEIGHT} L${firstX.toFixed(1)},${HEIGHT} Z`

  const first = sorted[0]
  const last = sorted[sorted.length - 1]

  return (
    <div className="trend-chart">
      <p className="trend-chart__label">최근 {sorted.length}일 {label} 추이</p>
      <svg
        className="trend-chart__svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${label} 시계열 그래프, ${first.date}부터 ${last.date}까지`}
      >
        <path d={areaPath} className="trend-chart__area" />
        <path d={linePath} className="trend-chart__line" />
      </svg>
      <div className="trend-chart__axis">
        <span>{formatShortDate(first.date)}</span>
        <span>{formatShortDate(last.date)}</span>
      </div>
    </div>
  )
}

const SCALE_MIN = -3
const SCALE_MAX = 3

// trend/zscore.py classify_trend()의 실제 임계값과 동일하게 맞춘 구간.
const ZONES = [
  { key: 'extinct', from: SCALE_MIN, to: -2, label: '소멸' },
  { key: 'down', from: -2, to: -0.5, label: '감소' },
  { key: 'neutral', from: -0.5, to: 0.5, label: '평상' },
  { key: 'up', from: 0.5, to: 2, label: '유행 중' },
  { key: 'hot', from: 2, to: SCALE_MAX, label: '핫함' },
]

function toPercent(z) {
  return ((z - SCALE_MIN) / (SCALE_MAX - SCALE_MIN)) * 100
}

export default function TrendGauge({ trend }) {
  if (!trend || trend.status === '데이터 부족') return null
  const z = trend.final_z ?? trend.z_score
  if (typeof z !== 'number') return null

  const clamped = Math.max(SCALE_MIN, Math.min(SCALE_MAX, z))
  const markerPct = toPercent(clamped)

  return (
    <div className="trend-gauge">
      <div className="trend-gauge__track">
        {ZONES.map((zone) => (
          <span
            key={zone.key}
            className={`trend-gauge__zone trend-gauge__zone--${zone.key}`}
            style={{ width: `${toPercent(zone.to) - toPercent(zone.from)}%` }}
          />
        ))}
        <span
          className="trend-gauge__marker"
          style={{ left: `${markerPct}%` }}
          title={`z ${z >= 0 ? '+' : ''}${z.toFixed(2)}`}
        >
          <span className="trend-gauge__marker-value">{z >= 0 ? '+' : ''}{z.toFixed(2)}</span>
        </span>
      </div>
      <div className="trend-gauge__labels">
        {ZONES.map((zone) => (
          <span key={zone.key} className={`trend-gauge__label trend-gauge__label--${zone.key}`}>
            {zone.label}
          </span>
        ))}
      </div>
      <p className="trend-gauge__caption">최근 언급량을 평소와 비교해 보여줘요</p>
    </div>
  )
}

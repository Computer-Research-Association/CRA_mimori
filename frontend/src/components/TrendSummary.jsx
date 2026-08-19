import TrendChart from './TrendChart.jsx'
import { pickPrimarySeries, computeChangeRate, trendDirectionLabel } from '../trendUtils.js'

// AnalysisPanel(분석 결과 화면)과 CrawlRequestPanel(신규 키워드 수집 중 화면) 양쪽에서 쓴다 —
// 트렌드 판정은 크롤링/임베딩과 완전히 독립적으로 끝나므로, 수십 분 걸리는 수집을
// 기다리지 않고도 먼저 보여줄 수 있다.
export default function TrendSummary({ trend }) {
  if (!trend) return null

  const z = trend.final_z ?? trend.z_score
  const primarySeries = pickPrimarySeries(trend)
  const changeRate = primarySeries ? computeChangeRate(primarySeries.points) : null
  const changeSentence = typeof changeRate === 'number' && primarySeries
    ? `최근 ${primarySeries.points.length}일간 ${primarySeries.label}가 평균보다 ${Math.abs(changeRate).toFixed(0)}% ${changeRate >= 0 ? '더 높다' : '더 낮다'}.`
    : trendDirectionLabel({ z })
      ? `${trendDirectionLabel({ z })}.`
      : null
  const zNote = typeof z === 'number' ? ` (z ${z >= 0 ? '+' : ''}${z.toFixed(2)})` : ''
  const trendLine = `트렌드: ${trend.status}.`

  return (
    <>
      <p className="trend-line">
        <strong className={trend.status === '핫함' ? 'trend-line__hot' : undefined}>{trendLine}</strong>
        {changeSentence && ` ${changeSentence}`}
        {zNote}
      </p>
      {primarySeries && <TrendChart points={primarySeries.points} label={primarySeries.label} />}
    </>
  )
}

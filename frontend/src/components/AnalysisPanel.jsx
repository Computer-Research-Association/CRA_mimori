import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchTrend, analyzeKeyword } from '../api.js'
import TrendChart from './TrendChart.jsx'
import { pickPrimarySeries, computeChangeRate } from '../trendUtils.js'

export default function AnalysisPanel({ keyword }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [sources, setSources] = useState([])
  const [trend, setTrend] = useState(null)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setResult(null)
    setSources([])
    setTrend(null)

    Promise.all([fetchTrend(keyword), analyzeKeyword(keyword)])
      .then(([trendData, analysisData]) => {
        if (cancelled) return
        setTrend(trendData)
        setResult(analysisData.result)
        setSources(analysisData.sources || [])
      })
      .catch((e) => {
        if (cancelled) return
        setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [keyword, retryCount])

  if (loading) {
    return <p className="loading-line"><span className="spinner" aria-hidden="true" />분석 중...</p>
  }
  if (error) {
    return (
      <div>
        <p role="alert" className="alert">{error}</p>
        <button className="btn btn--ghost" onClick={() => setRetryCount((c) => c + 1)}>다시 시도</button>
      </div>
    )
  }

  const z = trend?.final_z ?? trend?.z_score
  const primarySeries = pickPrimarySeries(trend)
  const changeRate = primarySeries ? computeChangeRate(primarySeries.points) : null
  const changeSentence = typeof changeRate === 'number'
    ? `최근 평균보다 ${Math.abs(changeRate).toFixed(0)}% ${changeRate >= 0 ? '더' : '덜'} 언급되고 있다.`
    : null
  const trendLine = trend ? `트렌드: ${trend.status}.` : null

  return (
    <div className="entry">
      {trendLine && (
        <p
          className="trend-line"
          title={typeof z === 'number' ? `z-score ${z >= 0 ? '+' : ''}${z.toFixed(2)}` : undefined}
        >
          {trend.status === '핫함' ? <strong className="trend-line__hot">{trendLine}</strong> : trendLine}
          {changeSentence && ` ${changeSentence}`}
        </p>
      )}
      {primarySeries && <TrendChart points={primarySeries.points} label={primarySeries.label} />}
      <div className="markdown-body">
        <ReactMarkdown>{result}</ReactMarkdown>
      </div>
      {sources.length > 0 && (
        <ul className="source-list">
          {sources.map((s, i) => (
            <li key={i}>
              <a href={s.url}>{s.title}</a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

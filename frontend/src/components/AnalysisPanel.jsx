import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchTrend, analyzeKeyword } from '../api.js'
import TrendGauge from './TrendGauge.jsx'

function TrendBadge({ trend }) {
  if (!trend) return null
  if (trend.status === '데이터 부족') return <p className="badge badge--none">트렌드: 데이터 부족</p>
  const z = trend.final_z ?? trend.z_score
  return (
    <p className="badge badge--hot">
      트렌드: {trend.status}
      {typeof z === 'number' && ` (z ${z >= 0 ? '+' : ''}${z.toFixed(2)})`}
    </p>
  )
}

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
    return (
      <div className="card">
        <p className="loading-line"><span className="spinner" aria-hidden="true" />분석 중...</p>
      </div>
    )
  }
  if (error) {
    return (
      <div className="card card--error">
        <p role="alert" className="alert">{error}</p>
        <button className="btn btn--ghost" onClick={() => setRetryCount((c) => c + 1)}>다시 시도</button>
      </div>
    )
  }

  return (
    <div className="card">
      <TrendBadge trend={trend} />
      <TrendGauge trend={trend} />
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

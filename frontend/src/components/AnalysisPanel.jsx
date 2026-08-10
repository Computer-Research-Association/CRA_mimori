import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchTrend, analyzeKeyword } from '../api.js'

function TrendBadge({ trend }) {
  if (!trend) return null
  if (trend.status === '데이터 부족') return <p>트렌드: 데이터 부족</p>
  const z = trend.final_z ?? trend.z_score
  return (
    <p>
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

  if (loading) return <p>분석 중...</p>
  if (error) {
    return (
      <div>
        <p role="alert">{error}</p>
        <button onClick={() => setRetryCount((c) => c + 1)}>다시 시도</button>
      </div>
    )
  }

  return (
    <div>
      <TrendBadge trend={trend} />
      <div className="markdown-body">
        <ReactMarkdown>{result}</ReactMarkdown>
      </div>
      {sources.length > 0 && (
        <ul>
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

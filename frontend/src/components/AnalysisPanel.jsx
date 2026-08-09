import { useEffect, useState } from 'react'
import { fetchTrend, analyzeKeyword } from '../api.js'

export default function AnalysisPanel({ keyword }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [trend, setTrend] = useState(null)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setResult(null)
    setTrend(null)

    Promise.all([fetchTrend(keyword), analyzeKeyword(keyword)])
      .then(([trendData, analysisData]) => {
        if (cancelled) return
        setTrend(trendData)
        setResult(analysisData.result)
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
      {trend && <p>트렌드: {trend.status}</p>}
      <p>{result}</p>
    </div>
  )
}

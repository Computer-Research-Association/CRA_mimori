import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'


export default function AnalysisPanel({ keyword }) {
  const [status, setStatus] = useState('queued')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [sources, setSources] = useState([])
  const [trend, setTrend] = useState(null)
  const [retryCount, setRetryCount] = useState(0)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)

  function startPolling() {
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchAnalyzeStatus(keyword)
        if (cancelledRef.current) return
        setStatus(data.status)
        if (data.status === 'done') {
          clearInterval(timerRef.current)
          setResult(data.result)
          setSources(data.sources || [])
        } else if (data.status === 'failed') {
          clearInterval(timerRef.current)
          setError(data.error)
        }
      } catch (e) {
        if (cancelledRef.current) return
        clearInterval(timerRef.current)
        setError(e.message || '네트워크 오류가 발생했습니다')
      }
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    cancelledRef.current = false
    setStatus('queued')
    setError(null)
    setResult(null)
    setSources([])
    setTrend(null)

    fetchTrend(keyword).then((data) => {
      if (!cancelledRef.current) setTrend(data)
    })

    submitAnalyzeRequest(keyword)
      .then(() => {
        if (!cancelledRef.current) startPolling()
      })
      .catch((e) => {
        if (!cancelledRef.current) setError(e.message || '네트워크 오류가 발생했습니다')
      })

    return () => {
      cancelledRef.current = true
      clearInterval(timerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword, retryCount])


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
  const changeSentence = typeof changeRate === 'number' && primarySeries
    ? `최근 ${primarySeries.points.length}일간 ${primarySeries.label}가 평균보다 ${Math.abs(changeRate).toFixed(0)}% ${changeRate >= 0 ? '더 높다' : '더 낮다'}.`
    : trendDirectionLabel({ z })
      ? `${trendDirectionLabel({ z })}.`
      : null
  const zNote = typeof z === 'number' ? ` (z ${z >= 0 ? '+' : ''}${z.toFixed(2)})` : ''
  const trendLine = trend ? `트렌드: ${trend.status}.` : null

  return (
    <div className="entry">
      {trendLine && (
        <p className="trend-line">
          {trend.status === '핫함' ? <strong className="trend-line__hot">{trendLine}</strong> : trendLine}
          {changeSentence && ` ${changeSentence}`}
          {zNote}
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

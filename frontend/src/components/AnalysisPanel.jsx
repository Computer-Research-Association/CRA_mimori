import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchTrend, submitAnalyzeRequest, fetchAnalyzeStatus } from '../api.js'
import TrendGauge from './TrendGauge.jsx'
import LoadingStages from './LoadingStages.jsx'
import SourceIcon from './SourceIcon.jsx'
import { sourceMetaForUrl } from '../sourceMeta.js'

const POLL_INTERVAL_MS = 3000

const ANALYZE_LOADING_MESSAGES = [
  '관련 커뮤니티 자료를 찾는 중...',
  '반응과 사용 맥락을 정리하는 중...',
  'AI가 분석을 작성하는 중...',
]

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
      {status !== 'done' ? (
        <LoadingStages messages={ANALYZE_LOADING_MESSAGES} />
      ) : (
        <>
          <div className="markdown-body">
            <ReactMarkdown>{result}</ReactMarkdown>
          </div>
          {sources.length > 0 && (
            <ul className="source-list">
              {sources.map((s, i) => (
                <li key={i}>
                  <SourceIcon meta={sourceMetaForUrl(s.url)} />
                  <a href={s.url}>{s.title}</a>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

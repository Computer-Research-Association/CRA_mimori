import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchAnalyzeStatus, submitAnalyzeRequest, fetchTrend } from '../api.js'
import TrendSummary from './TrendSummary.jsx'
import LoadingStages from './LoadingStages.jsx'
import SourceIcon from './SourceIcon.jsx'
import { sourceMetaForUrl } from '../sourceMeta.js'

const POLL_INTERVAL_MS = 3000

const ANALYZE_LOADING_MESSAGES = [
  '관련 커뮤니티 자료를 찾는 중...',
  '반응과 사용 맥락을 정리하는 중...',
  'AI가 분석을 작성하는 중...',
]

export default function AnalysisPanel({ keyword, selectedSources }) {
  const [status, setStatus] = useState('queued')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [partialResult, setPartialResult] = useState(null)
  const [sources, setSources] = useState([])
  const [trend, setTrend] = useState(null)
  const [retryCount, setRetryCount] = useState(0)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)

  function startPolling(jobId) {
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchAnalyzeStatus(jobId)
        if (cancelledRef.current) return
        setStatus(data.status)
        if (data.sources && data.sources.length > 0) setSources(data.sources)
        if (data.partial_result) setPartialResult(data.partial_result)
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
    setPartialResult(null)
    setSources([])
    setTrend(null)

    fetchTrend(keyword).then((data) => {
      if (!cancelledRef.current) setTrend(data)
    })

    if (selectedSources.length === 0) return

    submitAnalyzeRequest(keyword, selectedSources)
      .then((data) => {
        if (!cancelledRef.current) startPolling(data.job_id)
      })
      .catch((e) => {
        if (!cancelledRef.current) setError(e.message || '네트워크 오류가 발생했습니다')
      })

    return () => {
      cancelledRef.current = true
      clearInterval(timerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword, selectedSources, retryCount])


  if (error) {
    return (
      <div>
        <p role="alert" className="alert">{error}</p>
        <button className="btn btn--ghost" onClick={() => setRetryCount((c) => c + 1)}>다시 시도</button>
      </div>
    )
  }

  if (selectedSources.length === 0) {
    return <p className="loading-line">최소 하나의 출처를 선택하세요</p>
  }

  const loading = status === 'queued' || status === 'running'
  const displayText = result || partialResult

  return (
    <div className="entry">
      <TrendSummary trend={trend} />
      {loading && !displayText && <LoadingStages messages={ANALYZE_LOADING_MESSAGES} />}
      {displayText && (
        <>
          <div className="markdown-body">
            <ReactMarkdown>{displayText}</ReactMarkdown>
          </div>
          {loading && !result && <p className="loading-line">작성 중...</p>}
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

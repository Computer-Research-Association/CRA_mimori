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
  const [lowConfidence, setLowConfidence] = useState(false)
  const [trend, setTrend] = useState(null)
  const [retryCount, setRetryCount] = useState(0)
  const [justArrived, setJustArrived] = useState(false)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)
  const pulseTimerRef = useRef(null)
  const lastTextRef = useRef(null)

  // 스트리밍 답변이 몇 초 간격으로 통째로 갱신되며 뚝뚝 끊겨 나오는데(폴링 주기라
  // 진짜 타자 치듯 한 글자씩은 아님), 그렇다고 마크다운을 글자 단위로 잘라
  // 렌더링하면 "**볼드" 같은 미완성 문법이 그대로 노출되는 위험이 있다. 그 대신
  // 새 텍스트가 도착한 순간에만 짧게 살아나는 펄스로 "방금 갱신됨"을 알린다.
  function pulse() {
    setJustArrived(true)
    clearTimeout(pulseTimerRef.current)
    pulseTimerRef.current = setTimeout(() => setJustArrived(false), 500)
  }

  function startPolling(jobId) {
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchAnalyzeStatus(jobId)
        if (cancelledRef.current) return
        setStatus(data.status)
        if (data.sources && data.sources.length > 0) setSources(data.sources)
        if (data.sources) setLowConfidence(Boolean(data.low_confidence))
        if (data.partial_result && data.partial_result !== lastTextRef.current) {
          lastTextRef.current = data.partial_result
          setPartialResult(data.partial_result)
          pulse()
        }
        if (data.status === 'done') {
          clearInterval(timerRef.current)
          setResult(data.result)
          setSources(data.sources || [])
          pulse()
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
    setLowConfidence(false)
    setTrend(null)
    setJustArrived(false)
    lastTextRef.current = null

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
      clearTimeout(pulseTimerRef.current)
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
          {lowConfidence && (
            <p className="low-confidence-note">
              근거 자료가 적어 이 분석의 확신도가 낮아요. 소수 출처의 표현이 실제보다 널리 쓰이는
              뜻처럼 나올 수 있어요.
            </p>
          )}
          <div className={`markdown-body${justArrived ? ' markdown-body--updated' : ''}`}>
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

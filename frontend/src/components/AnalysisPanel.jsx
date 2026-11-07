import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchAnalyzeStatus, submitAnalyzeRequest, fetchTrend } from '../api.js'
import TrendSummary from './TrendSummary.jsx'
import LoadingStages from './LoadingStages.jsx'
import SourceIcon from './SourceIcon.jsx'
import { sourceMetaForUrl } from '../sourceMeta.js'

const POLL_INTERVAL_MS = 1000
const RETRIEVING_MESSAGES = ['저장된 자료에서 근거를 찾는 중...']
const GENERATING_MESSAGES = ['근거 자료를 바탕으로 분석을 작성하는 중...']

export default function AnalysisPanel({ keyword, selectedSources }) {
  const [status, setStatus] = useState('queued')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [partialResult, setPartialResult] = useState(null)
  const [sources, setSources] = useState([])
  const [lowConfidence, setLowConfidence] = useState(false)
  const [trend, setTrend] = useState(null)
  const [retryCount, setRetryCount] = useState(0)
  const [citationWarning, setCitationWarning] = useState(false)
  const [verification, setVerification] = useState(null)
  const displayText = result || partialResult

  useEffect(() => {
    // 각 요청의 취소 상태를 따로 보관해야 이전 키워드의 느린 응답이 새 결과를 덮지 않는다.
    let cancelled = false
    let timer
    setStatus('queued')
    setError(null)
    setResult(null)
    setPartialResult(null)
    setSources([])
    setLowConfidence(false)
    setCitationWarning(false)
    setVerification(null)
    setTrend(null)

    fetchTrend(keyword).then((data) => {
      if (!cancelled) setTrend(data)
    }).catch(() => {})

    async function poll(jobId) {
      try {
        const data = await fetchAnalyzeStatus(jobId)
        if (cancelled) return
        setStatus(data.status)
        if (data.sources) setSources(data.sources)
        setLowConfidence(Boolean(data.low_confidence))
        setCitationWarning(Boolean(data.citation_warning))
        setVerification(data.verification ?? null)
        if (data.partial_result) setPartialResult(data.partial_result)
        if (data.status === 'done') {
          setResult(data.result)
        } else if (data.status === 'failed') {
          setError(data.error || '분석을 완료하지 못했습니다')
        } else {
          // 한 요청이 끝난 뒤 다음 폴링을 예약해 느린 네트워크에서도 요청이 겹치지 않는다.
          timer = setTimeout(() => poll(jobId), POLL_INTERVAL_MS)
        }
      } catch (e) {
        if (!cancelled) setError(e.message || '네트워크 오류가 발생했습니다')
      }
    }

    if (selectedSources.length > 0) {
      submitAnalyzeRequest(keyword, selectedSources).then((data) => {
        if (!cancelled) poll(data.job_id)
      }).catch((e) => {
        if (!cancelled) setError(e.message || '네트워크 오류가 발생했습니다')
      })
    }
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
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
  const checkedDate = verification?.checked_at ? new Date(verification.checked_at) : null
  const checkedDateLabel = checkedDate && !Number.isNaN(checkedDate.valueOf())
    ? `${checkedDate.getUTCFullYear()}. ${String(checkedDate.getUTCMonth() + 1).padStart(2, '0')}. ${String(checkedDate.getUTCDate()).padStart(2, '0')}`
    : null

  return (
    <div className="entry">
      <TrendSummary trend={trend} />
      {loading && !displayText && <LoadingStages messages={sources.length ? GENERATING_MESSAGES : RETRIEVING_MESSAGES} />}
      {displayText && (
        <>
          {lowConfidence && (
            <p className="low-confidence-note">
              근거 자료가 적어 이 분석의 확신도가 낮아요. 소수 출처의 표현이 실제보다 널리 쓰이는
              뜻처럼 나올 수 있어요.
            </p>
          )}
          <p className="tier-section__caption">분석문은 생성 당시 자료에 근거해요. 현재 트렌드는 위 집계일을 확인해 주세요.</p>
          {checkedDateLabel && verification?.status === 'unchanged' && (
            <p className="tier-section__caption">근거 검증 기준일 · {checkedDateLabel}<br />이후 수집된 자료를 확인했지만, 답변을 바꿀 만큼 의미 있는 내용 변화는 없습니다.</p>
          )}
          {checkedDateLabel && verification?.status === 'updated' && (
            <p className="tier-section__caption">근거 검증 기준일 · {checkedDateLabel}<br />새 근거를 반영해 분석을 갱신했습니다.</p>
          )}
          {citationWarning && (
            <p className="low-confidence-note" role="status">일부 설명의 출처를 확인하지 못했어요. 연결된 자료를 직접 확인해 주세요.</p>
          )}
          <div className="markdown-body">
            <ReactMarkdown>{displayText}</ReactMarkdown>
          </div>
          {loading && !result && <p className="loading-line">작성 중...</p>}

        </>
      )}
      {sources.length > 0 && (
        <section className="analysis-sources" aria-label="분석 근거 자료">
          <h2 className="analysis-sources__title">근거 자료 {sources.length}개</h2>
          <ul className="source-list">
            {sources.map((s, i) => (
              <li key={`${s.url}-${i}`} id={`source-${i + 1}`}>
                <SourceIcon meta={sourceMetaForUrl(s.url)} />
                <a href={s.url} target="_blank" rel="noopener noreferrer"><span>[자료 {i + 1}]</span>{' '}<span>{s.title}</span></a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

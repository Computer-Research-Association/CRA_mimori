import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fetchAnalyzeStatus, submitAnalyzeRequest, fetchTrend } from '../api.js'
import TrendSummary from './TrendSummary.jsx'
import LoadingStages from './LoadingStages.jsx'
import SourceIcon from './SourceIcon.jsx'
import { sourceMetaForUrl } from '../sourceMeta.js'

const POLL_INTERVAL_MS = 3000

// 한 글자가 화면에 나타나기까지의 간격(ms) — 값이 작을수록 빠르게 타이핑된다.
const TYPE_MS_PER_CHAR = 18

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
  const [revealedChars, setRevealedChars] = useState(0)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)
  const lastTextRef = useRef(null)
  // 타이핑 진행 상태는 리렌더와 무관하게 계속 이어져야 해서(폴링으로 텍스트가
  // 늘어날 때마다 처음부터 다시 타이핑하면 안 됨) state가 아니라 ref로 들고,
  // requestAnimationFrame으로 시간에 비례해 한 글자씩 늘려간다. 마크다운을
  // 글자 단위로 잘라 파서에 넘기면 "**볼드"처럼 문법이 순간적으로 안 닫힌
  // 채로 보일 수 있지만(잠깐 별표가 그대로 보였다 사라지는 정도), remark가
  // 이를 에러 없이 그냥 텍스트로 처리하다가 닫히는 순간 정상 서식으로
  // 바뀌므로 기능적으로 깨지진 않는다 — 실제 타이핑 효과를 위한 의도된 트레이드오프.
  const revealRef = useRef({ shown: 0, target: 0, raf: null, lastTs: null })

  const displayText = result || partialResult

  function runReveal() {
    if (revealRef.current.raf) return
    function step(ts) {
      const state = revealRef.current
      if (state.lastTs == null) state.lastTs = ts
      const dt = ts - state.lastTs
      state.lastTs = ts
      state.shown = Math.min(state.target, state.shown + dt / TYPE_MS_PER_CHAR)
      setRevealedChars(Math.floor(state.shown))
      if (state.shown < state.target) {
        state.raf = requestAnimationFrame(step)
      } else {
        state.raf = null
        state.lastTs = null
      }
    }
    revealRef.current.raf = requestAnimationFrame(step)
  }

  // displayText가 늘어날 때마다(스트리밍 청크 도착, 또는 캐시 히트로 한 번에 전체
  // 텍스트가 옴) 목표 길이만 갱신하고 루프를 깨우지 않은 채 이어 타이핑한다.
  // 모션 최소화 설정(prefers-reduced-motion)이면 타이핑 애니메이션 없이 바로 전체를 보여준다.
  useEffect(() => {
    const len = displayText ? displayText.length : 0
    const reduceMotion =
      typeof window !== 'undefined' &&
      Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches)
    if (reduceMotion) {
      revealRef.current = { shown: len, target: len, raf: null, lastTs: null }
      setRevealedChars(len)
      return
    }
    revealRef.current.target = len
    if (len > revealRef.current.shown) runReveal()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayText])

  function startPolling(jobId) {
    async function tick() {
      try {
        const data = await fetchAnalyzeStatus(jobId)
        if (cancelledRef.current) return
        setStatus(data.status)
        if (data.sources && data.sources.length > 0) setSources(data.sources)
        if (data.sources) setLowConfidence(Boolean(data.low_confidence))
        if (data.partial_result && data.partial_result !== lastTextRef.current) {
          lastTextRef.current = data.partial_result
          setPartialResult(data.partial_result)
        }
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
    }
    // 캐시 히트(요청 시점에 이미 done)면 굳이 첫 폴링 주기(3초)만큼 기다렸다가
    // 보여줄 이유가 없다 — 바로 한 번 확인하고, 그 뒤로는 평소대로 주기적으로 본다.
    tick()
    timerRef.current = setInterval(tick, POLL_INTERVAL_MS)
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
    setRevealedChars(0)
    lastTextRef.current = null
    if (revealRef.current.raf) cancelAnimationFrame(revealRef.current.raf)
    revealRef.current = { shown: 0, target: 0, raf: null, lastTs: null }

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
      if (revealRef.current.raf) cancelAnimationFrame(revealRef.current.raf)
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
  const shownText = displayText ? displayText.slice(0, revealedChars) : displayText
  const isTyping = Boolean(displayText) && revealedChars < displayText.length

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
          <div className={`markdown-body${isTyping ? ' markdown-body--typing' : ''}`}>
            <ReactMarkdown>{shownText}</ReactMarkdown>
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

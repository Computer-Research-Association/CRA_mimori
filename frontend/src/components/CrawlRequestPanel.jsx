import { useEffect, useRef, useState } from 'react'
import { requestCrawl, fetchCrawlStatus, fetchTrend } from '../api.js'
import TrendSummary from './TrendSummary.jsx'

const POLL_INTERVAL_MS = 5000

// 워커가 기록하는 단계 코드 → 화면 문구. 모르는 코드는 그대로 보여준다(백엔드가
// 단계를 추가해도 프론트 배포 전까지 빈칸이 되지 않도록).
const STAGE_LABELS = {
  crawl: '웹에서 자료 수집 중',
  preprocess: '본문 정제 중',
  embed: '임베딩 생성 중',
}

const SOURCE_LABELS = {
  dcinside: '디시인사이드',
  namuwiki: '나무위키',
  natepann: '네이트판',
  todayhumor: '오늘의유머',
  youtube: '유튜브',
  tavily: 'Tavily 검색',
  duckduckgo: 'DuckDuckGo 검색',
}

function sourceResult({ count, status }) {
  if (status !== 'ok') return status  // '실패(HTTPError)' 등 사유를 그대로 노출
  return count > 0 ? `${count}건` : '자료 없음'
}

export default function CrawlRequestPanel({ keyword, onDone }) {
  const [status, setStatus] = useState('queued')
  const [stage, setStage] = useState(null)
  const [progress, setProgress] = useState({})
  const [error, setError] = useState(null)
  const [trend, setTrend] = useState(null)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)
  const trendFoundRef = useRef(false)

  // 트렌드 판정(네이버/카카오/구글)은 크롤링·임베딩과 완전히 독립적이라 워커가
  // 큐를 집자마자 몇 초 만에 끝난다(scripts/crawl_request_worker.py의 조기 판정 단계).
  // 수십 분 걸리는 수집을 기다리지 않고 먼저 보여주기 위해 크롤 상태와 별도로 폴링한다.
  // 한 번 찾으면 더 조회하지 않는다(같은 세션에서 값이 바뀔 일이 없음).
  async function pollTrend() {
    if (trendFoundRef.current) return
    try {
      const data = await fetchTrend(keyword)
      if (cancelledRef.current || !data) return
      trendFoundRef.current = true
      setTrend(data)
    } catch {
      // 부가 정보라 조회 실패해도 크롤링 진행 표시엔 영향 없음 — 다음 틱에 재시도.
    }
  }

  function startPolling() {
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchCrawlStatus(keyword)
        if (cancelledRef.current) return
        setStatus(data.status)
        setStage(data.stage ?? null)
        setProgress(data.progress ?? {})
        if (data.status === 'done') {
          clearInterval(timerRef.current)
          onDone(keyword)
          return
        } else if (data.status === 'failed') {
          clearInterval(timerRef.current)
          setError(data.error)
          return
        }
      } catch (e) {
        if (cancelledRef.current) return
        clearInterval(timerRef.current)
        setError(e.message || '네트워크 오류가 발생했습니다')
        return
      }
      pollTrend()
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    cancelledRef.current = false
    trendFoundRef.current = false
    setTrend(null)
    pollTrend()
    requestCrawl(keyword)
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
  }, [keyword])

  function handleRetry() {
    setError(null)
    setStatus('queued')
    setStage(null)
    setProgress({})
    requestCrawl(keyword)
      .then(() => {
        if (!cancelledRef.current) startPolling()
      })
      .catch((e) => {
        if (!cancelledRef.current) setError(e.message || '네트워크 오류가 발생했습니다')
      })
  }

  if (error) {
    return (
      <div>
        <p role="alert" className="alert">'{keyword}' 수집에 실패했습니다: {error}</p>
        <button className="btn btn--ghost" onClick={handleRetry}>다시 시도</button>
      </div>
    )
  }

  // 큐에 들어갔지만 워커가 아직 안 집은 상태 — 단계도 진행률도 없다.
  const headline = status === 'queued' && !stage
    ? '순서를 기다리는 중'
    : (STAGE_LABELS[stage] ?? '수집 중')
  const sources = Object.entries(progress)

  return (
    <div>
      <TrendSummary trend={trend} />
      <p className="loading-line">
        <span className="spinner" aria-hidden="true" />
        '{keyword}' {headline}... (전체 수십 분 소요)
      </p>
      {sources.length > 0 && (
        <ul className="progress-list">
          {sources.map(([source, result]) => (
            <li key={source}>
              <span className="progress-list__source">{SOURCE_LABELS[source] ?? source}</span>
              <span className="progress-list__result num-tabular">{sourceResult(result)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

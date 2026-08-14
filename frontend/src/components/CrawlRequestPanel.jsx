import { useEffect, useRef, useState } from 'react'
import { requestCrawl, fetchCrawlStatus } from '../api.js'

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
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)

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

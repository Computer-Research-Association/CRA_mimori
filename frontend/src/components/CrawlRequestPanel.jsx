import { useEffect, useRef, useState } from 'react'
import { requestCrawl, fetchCrawlStatus } from '../api.js'

const POLL_INTERVAL_MS = 5000

export default function CrawlRequestPanel({ keyword, onDone }) {
  const [status, setStatus] = useState('queued')
  const [error, setError] = useState(null)
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)

  function startPolling() {
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchCrawlStatus(keyword)
        if (cancelledRef.current) return
        setStatus(data.status)
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
      <div className="card card--error">
        <p role="alert" className="alert">'{keyword}' 수집에 실패했습니다: {error}</p>
        <button className="btn btn--ghost" onClick={handleRetry}>다시 시도</button>
      </div>
    )
  }

  return (
    <div className="card">
      <p className="loading-line">
        <span className="spinner" aria-hidden="true" />
        '{keyword}' 수집 중입니다... (보통 수십 분 소요, 잠시 기다려 주세요)
      </p>
    </div>
  )
}

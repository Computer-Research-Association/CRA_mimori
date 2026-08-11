import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { submitRagRequest, fetchRagStatus, AVAILABLE_SOURCES } from '../api.js'
import SourceFilter from './SourceFilter.jsx'

const POLL_INTERVAL_MS = 3000

export default function RagPanel({ keyword }) {
  const [selectedSources, setSelectedSources] = useState([...AVAILABLE_SOURCES])
  const [question, setQuestion] = useState('')
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [sources, setSources] = useState([])
  const timerRef = useRef(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    return () => {
      cancelledRef.current = true
      clearInterval(timerRef.current)
    }
  }, [])

  function startPolling(jobId) {
    clearInterval(timerRef.current)
    timerRef.current = setInterval(async () => {
      try {
        const data = await fetchRagStatus(jobId)
        if (cancelledRef.current) return
        setStatus(data.status)
        if (data.status === 'done') {
          clearInterval(timerRef.current)
          setAnswer(data.answer)
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

  async function handleSubmit(e) {
    e.preventDefault()
    if (!question.trim()) return
    cancelledRef.current = false
    setStatus('queued')
    setError(null)
    setAnswer(null)
    setSources([])
    try {
      const data = await submitRagRequest(keyword, question, selectedSources)
      if (cancelledRef.current) return
      startPolling(data.job_id)
    } catch (e) {
      if (!cancelledRef.current) setError(e.message)
    }
  }

  const loading = status === 'queued' || status === 'running'

  return (
    <div className="card">
      <SourceFilter selected={selectedSources} onChange={setSelectedSources} />
      <form className="form-row" onSubmit={handleSubmit}>
        <label htmlFor="rag-question" className="sr-only">질문</label>
        <input
          id="rag-question"
          className="input"
          aria-label="질문"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="더 궁금한 게 있으세요?"
        />
        <button type="submit" className="btn btn--primary" disabled={loading || selectedSources.length === 0}>질문하기</button>
      </form>
      {selectedSources.length === 0 && <p className="loading-line">최소 하나의 출처를 선택하세요</p>}
      {loading && <p className="loading-line"><span className="spinner" aria-hidden="true" />답변 생성 중...</p>}
      {error && <p role="alert" className="alert">{error}</p>}
      {answer && (
        <div>
          <div className="markdown-body">
            <ReactMarkdown>{answer}</ReactMarkdown>
          </div>
          <ul className="source-list">
            {sources.map((s, i) => (
              <li key={i}>
                <a href={s.url}>{s.title}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

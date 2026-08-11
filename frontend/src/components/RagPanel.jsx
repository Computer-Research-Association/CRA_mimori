import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { submitRagRequest, fetchRagStatus, AVAILABLE_SOURCES } from '../api.js'
import SourceFilter from './SourceFilter.jsx'
import LoadingStages from './LoadingStages.jsx'
import SourceIcon from './SourceIcon.jsx'
import { sourceMetaForUrl } from '../sourceMeta.js'

const POLL_INTERVAL_MS = 3000

const EXAMPLE_QUESTIONS = [
  '무슨 뜻이에요?',
  '왜 유행했나요?',
  '누가 주로 써요?',
  '어떻게 사용하나요?',
]

const RAG_LOADING_MESSAGES = [
  '관련 자료를 찾는 중...',
  'AI가 답변을 작성하는 중...',
]

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

  async function submitQuestion(text) {
    cancelledRef.current = false
    setStatus('queued')
    setError(null)
    setAnswer(null)
    setSources([])
    try {
      const data = await submitRagRequest(keyword, text, selectedSources)
      if (cancelledRef.current) return
      startPolling(data.job_id)
    } catch (e) {
      if (!cancelledRef.current) setError(e.message)
    }
  }

  function handleSubmit(e) {
    e.preventDefault()
    const trimmed = question.trim()
    if (!trimmed) return
    submitQuestion(trimmed)
  }

  function handleExampleClick(text) {
    setQuestion(text)
    submitQuestion(text)
  }

  const loading = status === 'queued' || status === 'running'

  return (
    <div className="card">
      <SourceFilter selected={selectedSources} onChange={setSelectedSources} />
      <div className="example-chips">
        <span className="example-chips__label">예시:</span>
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            className="chip chip--button"
            onClick={() => handleExampleClick(q)}
            disabled={loading || selectedSources.length === 0}
          >
            {q}
          </button>
        ))}
      </div>
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
      {loading && <LoadingStages messages={RAG_LOADING_MESSAGES} />}
      {error && <p role="alert" className="alert">{error}</p>}
      {answer && (
        <div>
          <div className="markdown-body">
            <ReactMarkdown>{answer}</ReactMarkdown>
          </div>
          <ul className="source-list">
            {sources.map((s, i) => (
              <li key={i}>
                <SourceIcon meta={sourceMetaForUrl(s.url)} />
                <a href={s.url}>{s.title}</a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

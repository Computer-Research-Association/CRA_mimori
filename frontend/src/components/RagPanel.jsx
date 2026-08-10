import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { askRag, AVAILABLE_SOURCES } from '../api.js'
import SourceFilter from './SourceFilter.jsx'

export default function RagPanel({ keyword }) {
  const [selectedSources, setSelectedSources] = useState([...AVAILABLE_SOURCES])
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [sources, setSources] = useState([])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    setAnswer(null)
    try {
      const data = await askRag(keyword, question, selectedSources)
      setAnswer(data.answer)
      setSources(data.sources)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <SourceFilter selected={selectedSources} onChange={setSelectedSources} />
      <form onSubmit={handleSubmit}>
        <label htmlFor="rag-question">질문</label>
        <input
          id="rag-question"
          aria-label="질문"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="더 궁금한 게 있으세요?"
        />
        <button type="submit" disabled={loading || selectedSources.length === 0}>질문하기</button>
      </form>
      {selectedSources.length === 0 && <p>최소 하나의 출처를 선택하세요</p>}
      {loading && <p>답변 생성 중...</p>}
      {error && <p role="alert">{error}</p>}
      {answer && (
        <div>
          <div className="markdown-body">
            <ReactMarkdown>{answer}</ReactMarkdown>
          </div>
          <ul>
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

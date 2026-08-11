import { useEffect, useState } from 'react'
import { fetchKeywords } from './api.js'
import KeywordSelector from './components/KeywordSelector.jsx'
import KeywordManager from './components/KeywordManager.jsx'
import CrawlRequestPanel from './components/CrawlRequestPanel.jsx'
import AnalysisPanel from './components/AnalysisPanel.jsx'
import RagPanel from './components/RagPanel.jsx'

export default function App() {
  const [keywords, setKeywords] = useState([])
  const [keywordsLoaded, setKeywordsLoaded] = useState(false)
  const [keywordsError, setKeywordsError] = useState(null)
  const [selectedKeyword, setSelectedKeyword] = useState(null)
  const [pendingKeyword, setPendingKeyword] = useState(null)

  useEffect(() => {
    fetchKeywords()
      .then((kws) => {
        setKeywords(kws)
        setKeywordsLoaded(true)
      })
      .catch((e) => setKeywordsError(e.message || '키워드 목록을 불러오지 못했습니다'))
  }, [])

  function handleSelect(keyword) {
    setPendingKeyword(null)
    setSelectedKeyword(keyword)
  }

  function handleNewKeyword(keyword) {
    setSelectedKeyword(null)
    setPendingKeyword(keyword)
  }

  function handleCrawlDone(keyword) {
    setKeywords((prev) => [...prev, keyword])
    setPendingKeyword(null)
    setSelectedKeyword(keyword)
  }

  function handleKeywordHidden(keyword) {
    setKeywords((prev) => prev.filter((k) => k !== keyword))
    setSelectedKeyword((prev) => (prev === keyword ? null : prev))
  }

  function handleKeywordUnhidden(keyword) {
    setKeywords((prev) => (prev.includes(keyword) ? prev : [...prev, keyword].sort()))
  }

  function handleKeywordDeleted(keyword) {
    setSelectedKeyword((prev) => (prev === keyword ? null : prev))
  }

  return (
    <div>
      <header className="app-header">
        <h1 className="app-title">mimori</h1>
        <p className="app-subtitle">밈·신조어 검색</p>
      </header>
      <main className="app-main">
        {keywordsError && <p role="alert" className="alert">{keywordsError}</p>}
        {keywordsLoaded || keywordsError ? (
          <>
            <KeywordSelector keywords={keywords} onSelect={handleSelect} onNewKeyword={handleNewKeyword} />
            <KeywordManager
              keywords={keywords}
              onHidden={handleKeywordHidden}
              onUnhidden={handleKeywordUnhidden}
              onDeleted={handleKeywordDeleted}
            />
          </>
        ) : (
          <p className="loading-line"><span className="spinner" aria-hidden="true" />불러오는 중...</p>
        )}
        {pendingKeyword && <CrawlRequestPanel key={pendingKeyword} keyword={pendingKeyword} onDone={handleCrawlDone} />}
        {selectedKeyword && (
          <div>
            <div className="keyword-hero">
              <span className="eyebrow">검색 결과</span>
              <h2 className="keyword-hero__title">{selectedKeyword}</h2>
            </div>
            <div className="result-grid">
              <AnalysisPanel key={`analysis-${selectedKeyword}`} keyword={selectedKeyword} />
              <RagPanel key={`rag-${selectedKeyword}`} keyword={selectedKeyword} />
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

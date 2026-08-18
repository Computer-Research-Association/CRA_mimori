import { useEffect, useState } from 'react'
import { fetchKeywords, AVAILABLE_SOURCES } from './api.js'
import KeywordSelector from './components/KeywordSelector.jsx'
import CrawlRequestPanel from './components/CrawlRequestPanel.jsx'
import AnalysisPanel from './components/AnalysisPanel.jsx'
import SourceFilter from './components/SourceFilter.jsx'
import TierList from './components/TierList.jsx'
import AdminLink from './components/AdminLink.jsx'

export default function App() {
  const [keywords, setKeywords] = useState([])
  const [keywordsLoaded, setKeywordsLoaded] = useState(false)
  const [keywordsError, setKeywordsError] = useState(null)
  const [selectedKeyword, setSelectedKeyword] = useState(null)
  const [pendingKeyword, setPendingKeyword] = useState(null)
  const [selectedSources, setSelectedSources] = useState([...AVAILABLE_SOURCES])

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

  function handleLogoClick() {
    setSelectedKeyword(null)
    setPendingKeyword(null)
  }

  // Determine which screen to show
  const isResultsScreen = !!(selectedKeyword || pendingKeyword)

  // ── Home screen ───────────────────────────────────────────────────────────
  if (!isResultsScreen) {
    return (
      <div className="home-screen">
        <AdminLink className="home-screen__admin-link" />
        <div className="home-hero">
          <div className="home-hero__wordmark">MEMEMORY</div>
          <p className="home-hero__subtitle">밈·신조어 검색</p>

          {keywordsError && (
            <p role="alert" className="alert home-hero__alert">{keywordsError}</p>
          )}

          {keywordsLoaded || keywordsError ? (
            <>
              <KeywordSelector
                keywords={keywords}
                onSelect={handleSelect}
                onNewKeyword={handleNewKeyword}
                variant="hero"
                disabled={selectedSources.length === 0}
              />
              <SourceFilter selected={selectedSources} onChange={setSelectedSources} />
              {selectedSources.length === 0 && (
                <p className="loading-line">최소 하나의 출처를 선택하세요</p>
              )}
              <TierList onSelect={handleSelect} />
            </>
          ) : (
            <div className="home-hero__loading">
              <span className="spinner" aria-hidden="true" />
              <span>불러오는 중...</span>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── Results screen ────────────────────────────────────────────────────────
  return (
    <div className="results-screen">
      {/* Sticky top bar */}
      <header className="results-topbar">
        <button
          className="results-topbar__logo"
          onClick={handleLogoClick}
          aria-label="홈으로 돌아가기"
        >
          MEMEMORY
        </button>

        <div className="results-topbar__search">
          {keywordsLoaded && (
            <KeywordSelector
              keywords={keywords}
              onSelect={handleSelect}
              onNewKeyword={handleNewKeyword}
              variant="compact"
              currentKeyword={selectedKeyword || pendingKeyword}
            />
          )}
        </div>

        <div className="results-topbar__actions">
          <AdminLink className="btn btn--ghost" />
        </div>
      </header>

      {/* Results body */}
      <main className="results-body">
        {keywordsError && (
          <p role="alert" className="alert">{keywordsError}</p>
        )}

        {pendingKeyword && (
          <CrawlRequestPanel
            key={pendingKeyword}
            keyword={pendingKeyword}
            onDone={handleCrawlDone}
          />
        )}

        {selectedKeyword && (
          <>
            <div className="keyword-hero">
              <h1 className="keyword-hero__title">{selectedKeyword}</h1>
            </div>
            <AnalysisPanel
              key={`analysis-${selectedKeyword}`}
              keyword={selectedKeyword}
              selectedSources={selectedSources}
            />
          </>
        )}
      </main>
    </div>
  )
}

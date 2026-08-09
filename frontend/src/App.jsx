import { useEffect, useState } from 'react'
import { fetchKeywords } from './api.js'
import KeywordSelector from './components/KeywordSelector.jsx'
import CrawlRequestPanel from './components/CrawlRequestPanel.jsx'
import AnalysisPanel from './components/AnalysisPanel.jsx'
import RagPanel from './components/RagPanel.jsx'

export default function App() {
  const [keywords, setKeywords] = useState([])
  const [selectedKeyword, setSelectedKeyword] = useState(null)
  const [pendingKeyword, setPendingKeyword] = useState(null)

  useEffect(() => {
    fetchKeywords().then(setKeywords)
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

  return (
    <div>
      <h1>mimori — 밈/신조어 검색</h1>
      <KeywordSelector keywords={keywords} onSelect={handleSelect} onNewKeyword={handleNewKeyword} />
      {pendingKeyword && <CrawlRequestPanel keyword={pendingKeyword} onDone={handleCrawlDone} />}
      {selectedKeyword && (
        <>
          <AnalysisPanel keyword={selectedKeyword} />
          <RagPanel keyword={selectedKeyword} />
        </>
      )}
    </div>
  )
}

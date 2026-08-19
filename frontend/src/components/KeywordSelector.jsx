import { useEffect, useRef, useState } from 'react'
import { findSimilarKeywords } from '../keywordMatch.js'

/**
 * KeywordSelector — 두 가지 변형(variant)을 지원.
 *
 * variant="hero"    : 홈 화면의 큼직한 검색 입력 (max-width 600px, height 52px)
 * variant="compact" : 결과 상단 바의 작은 검색 입력 (topbar 안에 들어가는 크기)
 */
export default function KeywordSelector({ keywords, onSelect, onNewKeyword, variant = 'hero', currentKeyword, disabled = false }) {
  const [value, setValue] = useState(currentKeyword || '')
  // 완전일치는 아니지만 표기 차이·포함 관계로 "혹시 이거?" 싶은 기존 키워드들.
  // 비어있지 않으면 확인창을 보여주고 실제 등록/이동은 보류한다.
  const [similarCandidates, setSimilarCandidates] = useState([])
  const [pendingValue, setPendingValue] = useState('')
  const inputRef = useRef(null)

  // currentKeyword가 외부에서 바뀌면 input 값도 동기화
  useEffect(() => {
    setValue(currentKeyword || '')
  }, [currentKeyword])

  function handleSubmit(e) {
    e.preventDefault()
    if (disabled) return
    const trimmed = value.trim()
    if (!trimmed) return
    if (keywords.includes(trimmed)) {
      onSelect(trimmed)
      return
    }
    const similar = findSimilarKeywords(trimmed, keywords)
    if (similar.length > 0) {
      setSimilarCandidates(similar)
      setPendingValue(trimmed)
      return
    }
    onNewKeyword(trimmed)
    if (variant === 'compact') {
      inputRef.current?.blur()
    }
  }

  function chooseCandidate(kw) {
    setSimilarCandidates([])
    setPendingValue('')
    onSelect(kw)
    if (variant === 'compact') {
      inputRef.current?.blur()
    }
  }

  function confirmNewKeyword() {
    const trimmed = pendingValue
    setSimilarCandidates([])
    setPendingValue('')
    onNewKeyword(trimmed)
    if (variant === 'compact') {
      inputRef.current?.blur()
    }
  }

  function cancelSimilarConfirm() {
    setSimilarCandidates([])
    setPendingValue('')
  }

  const similarConfirm = similarCandidates.length > 0 && (
    <div className="similar-keyword-confirm" role="alert">
      <p>
        혹시 {similarCandidates.map((kw, i) => (
          <span key={kw}>
            {i > 0 && ', '}
            <button
              type="button"
              className="similar-keyword-confirm__candidate"
              onClick={() => chooseCandidate(kw)}
            >
              '{kw}'
            </button>
          </span>
        ))}를 찾으시나요?
      </p>
      <div className="similar-keyword-confirm__actions">
        <button type="button" className="btn btn--sm btn--ghost" onClick={confirmNewKeyword}>
          아니요, '{pendingValue}'(으)로 새로 등록
        </button>
        <button type="button" className="btn btn--sm btn--ghost" onClick={cancelSimilarConfirm}>
          취소
        </button>
      </div>
    </div>
  )

  if (variant === 'compact') {
    return (
      <>
        <form className="search-form search-form--compact" onSubmit={handleSubmit}>
          <div className="search-form__inner">
            <input
              ref={inputRef}
              className="search-form__input"
              list="keyword-options-compact"
              aria-label="키워드 검색"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="다른 밈 검색..."
            />
            <button type="submit" className="search-form__btn" aria-label="검색">
              <SearchIcon />
            </button>
          </div>
          <datalist id="keyword-options-compact">
            {keywords.map((kw) => (
              <option key={kw} value={kw} />
            ))}
          </datalist>
        </form>
        {similarConfirm}
      </>
    )
  }

  // hero variant
  return (
    <>
    <form className="search-form search-form--hero" onSubmit={handleSubmit}>
      <div className="search-form__inner">
        <input
          ref={inputRef}
          className="search-form__input"
          list="keyword-options-hero"
          aria-label="키워드 검색"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="밈/신조어를 입력하세요"
          autoFocus
        />
        <button type="submit" className="search-form__btn" aria-label="검색" disabled={disabled}>
          <SearchIcon />
        </button>
      </div>
      <datalist id="keyword-options-hero">
        {keywords.map((kw) => (
          <option key={kw} value={kw} />
        ))}
      </datalist>
    </form>
    {similarConfirm}
    </>
  )
}

function SearchIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}

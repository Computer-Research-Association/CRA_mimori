import { useEffect, useRef, useState } from 'react'

/**
 * KeywordSelector — 두 가지 변형(variant)을 지원.
 *
 * variant="hero"    : 홈 화면의 큼직한 검색 입력 (max-width 600px, height 52px)
 * variant="compact" : 결과 상단 바의 작은 검색 입력 (topbar 안에 들어가는 크기)
 */
export default function KeywordSelector({ keywords, onSelect, onNewKeyword, variant = 'hero', currentKeyword, disabled = false }) {
  const [value, setValue] = useState(currentKeyword || '')
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
    } else {
      onNewKeyword(trimmed)
    }
    if (variant === 'compact') {
      inputRef.current?.blur()
    }
  }

  if (variant === 'compact') {
    return (
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
    )
  }

  // hero variant
  return (
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

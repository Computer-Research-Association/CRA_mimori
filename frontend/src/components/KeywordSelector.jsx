import { useState } from 'react'

export default function KeywordSelector({ keywords, onSelect, onNewKeyword }) {
  const [value, setValue] = useState('')

  function handleSubmit(e) {
    e.preventDefault()
    const trimmed = value.trim()
    if (!trimmed) return
    if (keywords.includes(trimmed)) {
      onSelect(trimmed)
    } else {
      onNewKeyword(trimmed)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="밈/신조어를 입력하세요"
      />
      <button type="submit">검색</button>
    </form>
  )
}

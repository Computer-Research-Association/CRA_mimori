import { useEffect, useState } from 'react'
import { fetchHiddenKeywords, hideKeyword, unhideKeyword, deleteKeywordPermanently } from '../api.js'

export default function KeywordManager({ keywords, onHidden, onUnhidden, onDeleted }) {
  const [open, setOpen] = useState(false)
  const [hiddenKeywords, setHiddenKeywords] = useState([])
  const [error, setError] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [confirmText, setConfirmText] = useState('')

  useEffect(() => {
    if (!open) return
    fetchHiddenKeywords()
      .then(setHiddenKeywords)
      .catch((e) => setError(e.message))
  }, [open])

  async function handleHide(keyword) {
    setError(null)
    try {
      await hideKeyword(keyword)
      setHiddenKeywords((prev) => [...prev, keyword].sort())
      onHidden(keyword)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleUnhide(keyword) {
    setError(null)
    try {
      await unhideKeyword(keyword)
      setHiddenKeywords((prev) => prev.filter((k) => k !== keyword))
      onUnhidden(keyword)
    } catch (e) {
      setError(e.message)
    }
  }

  function startDelete(keyword) {
    setConfirmDelete(keyword)
    setConfirmText('')
  }

  function cancelDelete() {
    setConfirmDelete(null)
    setConfirmText('')
  }

  async function handleDeleteConfirmed(keyword) {
    setError(null)
    try {
      await deleteKeywordPermanently(keyword)
      setHiddenKeywords((prev) => prev.filter((k) => k !== keyword))
      setConfirmDelete(null)
      setConfirmText('')
      onDeleted(keyword)
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div>
      <button className="btn btn--ghost" onClick={() => setOpen((o) => !o)}>
        {open ? '키워드 관리 닫기' : '키워드 관리'}
      </button>
      {open && (
        <div className="card manager-body">
          {error && <p role="alert" className="alert">{error}</p>}

          <h3>검색 목록</h3>
          {keywords.length === 0 && <p>키워드가 없습니다</p>}
          <ul className="keyword-list">
            {keywords.map((kw) => (
              <li key={kw}>
                {kw}
                <span className="keyword-list__actions">
                  <button className="btn btn--sm btn--ghost" onClick={() => handleHide(kw)}>숨기기</button>
                </span>
              </li>
            ))}
          </ul>

          <h3>숨긴 키워드</h3>
          {hiddenKeywords.length === 0 && <p>숨긴 키워드가 없습니다</p>}
          <ul className="keyword-list">
            {hiddenKeywords.map((kw) => (
              <li key={kw}>
                {kw}
                <span className="keyword-list__actions">
                  <button className="btn btn--sm btn--ghost" onClick={() => handleUnhide(kw)}>복구</button>
                  {confirmDelete === kw ? (
                    <>
                      <input
                        className="input"
                        aria-label={`${kw} 완전삭제 확인 입력`}
                        value={confirmText}
                        onChange={(e) => setConfirmText(e.target.value)}
                        placeholder="확인을 위해 키워드를 그대로 입력"
                      />
                      <button
                        className="btn btn--sm btn--danger"
                        disabled={confirmText !== kw}
                        onClick={() => handleDeleteConfirmed(kw)}
                      >
                        완전삭제 확정
                      </button>
                      <button className="btn btn--sm btn--ghost" onClick={cancelDelete}>취소</button>
                    </>
                  ) : (
                    <button className="btn btn--sm btn--danger" onClick={() => startDelete(kw)}>완전삭제</button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

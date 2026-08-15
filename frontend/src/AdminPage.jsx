import { Fragment, useEffect, useState } from 'react'
import {
  fetchKeywords, fetchHiddenKeywords, hideKeyword, unhideKeyword,
  deleteKeywordPermanently, fetchAdminStats, getAdminKey, setAdminKey,
} from './api.js'
import { sourceMetaFor } from './sourceMeta.js'

const COLLECTION_LABELS = {
  memes: '원문 (memes)',
  cleaned_memes: '정제 문서 (cleaned_memes)',
  trend_scores: '트렌드 판정 (trend_scores)',
  crawl_requests: '수집 요청 큐 (crawl_requests)',
  hidden_keywords: '숨긴 키워드',
  llm_requests: '분석 요청 큐/캐시 (llm_requests)',
  crawl_rejects: '크롤 제외 이력 (crawl_rejects)',
}

export default function AdminPage() {
  const [keywords, setKeywords] = useState([])
  const [hiddenKeywords, setHiddenKeywords] = useState([])
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [confirmText, setConfirmText] = useState('')
  const [expandedKeyword, setExpandedKeyword] = useState(null)
  const [adminKeyInput, setAdminKeyInput] = useState(getAdminKey())

  function loadAdminData() {
    fetchHiddenKeywords().then(setHiddenKeywords).catch((e) => setError(e.message))
    fetchAdminStats().then(setStats).catch((e) => setError(e.message))
  }

  useEffect(() => {
    fetchKeywords().then(setKeywords).catch((e) => setError(e.message))
    loadAdminData()
  }, [])

  function handleApplyAdminKey(e) {
    e.preventDefault()
    setError(null)
    setAdminKey(adminKeyInput)
    loadAdminData()
  }

  async function handleHide(keyword) {
    setError(null)
    try {
      await hideKeyword(keyword)
      setKeywords((prev) => prev.filter((k) => k !== keyword))
      setHiddenKeywords((prev) => [...prev, keyword].sort())
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleUnhide(keyword) {
    setError(null)
    try {
      await unhideKeyword(keyword)
      setHiddenKeywords((prev) => prev.filter((k) => k !== keyword))
      setKeywords((prev) => (prev.includes(keyword) ? prev : [...prev, keyword].sort()))
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
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="admin-page">
      <header className="admin-page__header">
        <a href="/" className="admin-page__logo">mimori</a>
        <h1 className="admin-page__title">관리자 화면</h1>
        <form className="admin-key-form" onSubmit={handleApplyAdminKey}>
          <input
            type="password"
            className="input"
            aria-label="관리자 키"
            placeholder="관리자 키"
            value={adminKeyInput}
            onChange={(e) => setAdminKeyInput(e.target.value)}
          />
          <button type="submit" className="btn btn--sm">적용</button>
        </form>
      </header>

      <main className="admin-page__body">
        {error && <p role="alert" className="alert">{error}</p>}

        <section className="admin-page__section">
          <h2>서버 현황</h2>
          {stats && (
            <>
              {stats.disk_usage ? (
                <div className="admin-disk">
                  <div className="admin-disk__bar">
                    <div
                      className={`admin-disk__bar-fill${stats.disk_usage.used_percent >= 85 ? ' admin-disk__bar-fill--warn' : ''}`}
                      style={{ width: `${Math.min(100, stats.disk_usage.used_percent)}%` }}
                    />
                  </div>
                  <p className="admin-disk__summary">
                    디스크 <strong className="num-tabular">{stats.disk_usage.used_gb}GB</strong> 사용 중
                    {' '}/ 전체 <span className="num-tabular">{stats.disk_usage.total_gb}GB</span>
                    {' '}(<span className="num-tabular">{stats.disk_usage.used_percent}%</span>) ·
                    {' '}남은 공간 <strong className="num-tabular">{stats.disk_usage.free_gb}GB</strong>
                    {stats.disk_usage.used_percent >= 85 && <span className="badge badge--warn"> 여유 공간 부족</span>}
                  </p>
                </div>
              ) : (
                <p className="admin-page__hint">디스크 사용량을 읽을 수 없습니다.</p>
              )}

              <p className="admin-stats__summary">
                등록 키워드 <strong className="num-tabular">{stats.keyword_count}</strong> / {stats.keyword_cap}개
                {stats.keyword_count >= stats.keyword_cap && (
                  <span className="badge badge--warn"> 상한 도달</span>
                )}
              </p>

              {stats.capped_keywords.length > 0 && (
                <p role="alert" className="alert">
                  상한에 걸려 등록되지 못한 키워드가 {stats.capped_keywords.length}개 있어요
                  ({stats.capped_keywords.join(', ')}). 아래 목록에서 오래된/비인기 키워드를
                  정리하면 다음 신규 키워드부터 다시 등록됩니다.
                </p>
              )}

              <table className="admin-stats__table">
                <tbody>
                  {Object.entries(stats.collection_counts).map(([key, count]) => (
                    <tr key={key}>
                      <td>{COLLECTION_LABELS[key] ?? key}</td>
                      <td className="num-tabular">{count.toLocaleString()}건</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>

        <section className="admin-page__section">
          <h2>키워드별 원문 문서 — 출처별 분해</h2>
          <p className="admin-page__hint">키워드를 누르면 어느 출처에서 몇 건 왔는지 펼쳐집니다.</p>
          {stats && (
            <table className="admin-source-table">
              <thead>
                <tr>
                  <th>키워드</th>
                  <th className="num-tabular">합계</th>
                </tr>
              </thead>
              <tbody>
                {stats.per_keyword_doc_counts.map((row) => {
                  const isOpen = expandedKeyword === row.keyword
                  const sources = Object.entries(row.by_source).sort((a, b) => b[1] - a[1])
                  return (
                    <Fragment key={row.keyword}>
                      <tr
                        className="admin-source-table__row"
                        onClick={() => setExpandedKeyword(isOpen ? null : row.keyword)}
                      >
                        <td>{isOpen ? '▾' : '▸'} {row.keyword}</td>
                        <td className="num-tabular">{row.total.toLocaleString()}건</td>
                      </tr>
                      {isOpen && (
                        <tr className="admin-source-table__detail">
                          <td colSpan={2}>
                            {sources.length === 0 ? (
                              <span className="admin-page__hint">출처 정보 없음</span>
                            ) : (
                              <ul className="admin-source-list">
                                {sources.map(([source, count]) => (
                                  <li key={source}>
                                    <span>{sourceMetaFor(source).label}</span>
                                    <span className="num-tabular">{count.toLocaleString()}건</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          )}
        </section>

        <section className="admin-page__section">
          <h2>검색 목록</h2>
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
        </section>

        <section className="admin-page__section">
          <h2>숨긴 키워드</h2>
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
        </section>
      </main>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { fetchTrendLeaderboard } from '../api.js'
import { tierPillClass } from '../trendUtils.js'

function formatZ(row) {
  const z = row.final_z ?? row.z_score
  if (typeof z !== 'number') return null
  return `${z >= 0 ? '+' : ''}${z.toFixed(2)}`
}

export default function TierList({ onSelect }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchTrendLeaderboard()
      .then((data) => {
        if (!cancelled) setRows(data)
      })
      .catch((e) => {
        if (!cancelled) setError(e.message || '순위를 불러오지 못했습니다')
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return <p role="alert" className="alert">{error}</p>
  if (!rows) return null
  if (rows.length === 0) return null

  // 배치 대상 중 같은 집계일인 자료만 순위를 매긴다. 이전 자료는 검색 가능한 보관 목록이다.
  const dailyRows = rows.filter((row) => row.daily_refresh !== false)
  const latestDate = dailyRows.reduce((latest, row) => row.date > latest ? row.date : latest, '')
  const current = dailyRows.filter((row) => row.date && row.date === latestDate)
  const ranked = current.filter((row) => row.status && row.status !== '데이터 부족')
  const pending = rows.filter((row) => row.status === null)
  const noSignal = current.filter((row) => row.status === '데이터 부족')
  const archived = rows.filter((row) => row.status !== null && !current.includes(row))
  // 새벽 2시 전에는 전날 집계가 정상이다.
  const expectedDateKst = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date(Date.now() - 2 * 60 * 60 * 1000))

  return (
    <section className="tier-section">
      {latestDate && latestDate < expectedDateKst && (
        <p className="tier-section__caption" role="status">
          최신 집계는 {latestDate} 자료예요. 오늘 집계가 아직 반영되지 않았어요.
        </p>
      )}
      {ranked.length > 0 && (
        <>
          <div className="tier-section__head">
            <h2 className="tier-section__title">최근 집계 순위</h2>
            <span className="tier-section__meta">{latestDate} 집계 · 검색 관심도 z-score 기준</span>
          </div>
          <p className="tier-section__caption">
            같은 집계일의 정기 갱신 키워드만 비교해요. 정기 수집은 매일 새벽 2시에 시작하며, 분석문은 검색 시 작성해 저장해요.
          </p>
          <ul className="tier-list">
            {ranked.map((row, i) => (
              <li key={row.keyword}>
                <button
                  type="button"
                  className={`tier-row${row.status === '핫함' ? ' tier-row--hot' : ''}`}
                  onClick={() => onSelect(row.keyword)}
                >
                  <span className="tier-row__rank num-tabular">{i + 1}</span>
                  <span className="tier-row__word">
                    {row.keyword}
                    <small style={{ display: 'block', fontWeight: 400, fontSize: '0.7em' }}>
                      {row.date ? `${row.date} 기준` : '집계일 미확인'}
                    </small>
                  </span>
                  <span className={`tier-pill ${tierPillClass(row.status)}`}>{row.status}</span>
                  <span
                    className={`tier-row__z num-tabular${
                      (row.final_z ?? row.z_score) >= 0 ? ' tier-row__z--pos' : ' tier-row__z--neg'
                    }`}
                  >
                    {formatZ(row)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {archived.length > 0 && (
        <div className="unknown-section">
          <h2 className="tier-section__title tier-section__title--sub">이전 집계 · 보관 자료</h2>
          <p className="tier-section__caption">
            정기 갱신 대상이 아니거나 집계일이 다른 자료예요. 최근 집계 순위에는 포함하지 않아요.
          </p>
          <ul className="unknown-list">
            {archived.map((row) => (
              <li key={row.keyword}>
                <button type="button" className="unknown-chip" onClick={() => onSelect(row.keyword)}>
                  {row.keyword} · {row.date || '집계일 미확인'}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {pending.length > 0 && (
        <div className="unknown-section">
          <h2 className="tier-section__title tier-section__title--sub">막 등록됨 · 순위 집계 전</h2>
          <p className="tier-section__caption">
            아직 트렌드 집계 결과가 없는 키워드예요. 집계 결과가 생기면 순위에 반영돼요.
          </p>
          <ul className="unknown-list">
            {pending.map((row) => (
              <li key={row.keyword}>
                <button type="button" className="unknown-chip" onClick={() => onSelect(row.keyword)}>
                  {row.keyword}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {noSignal.length > 0 && (
        <div className="unknown-section">
          <h2 className="tier-section__title tier-section__title--sub">신호 부족</h2>
          <p className="tier-section__caption">
            네이버·카카오·구글 어디서도 뚜렷한 검색 신호를 찾지 못했어요. 아직 덜 알려졌거나
            검색으로는 잘 안 잡히는 표현일 수 있어요 — 추가 집계에서 충분한 신호가 확인되면 순위에 반영해요.
          </p>
          <ul className="unknown-list">
            {noSignal.map((row) => (
              <li key={row.keyword}>
                <button type="button" className="unknown-chip" onClick={() => onSelect(row.keyword)}>
                  {row.keyword}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

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

  // status가 null(trend_scores 문서 자체가 없음)이거나 "데이터 부족"(문서는 있지만
  // classify_trend가 신호 부족으로 판정)이면 둘 다 순위를 매길 수 없는 상태다.
  // 두 경우 모두 실제 순위 목록에서 빼고 "집계 전" 그룹으로 묶는다.
  const isUnranked = (row) => row.status === null || row.status === '데이터 부족'
  const ranked = rows.filter((row) => !isUnranked(row))
  const unranked = rows.filter(isUnranked)

  return (
    <section className="tier-section">
      {ranked.length > 0 && (
        <>
          <div className="tier-section__head">
            <h2 className="tier-section__title">지금 순위</h2>
            <span className="tier-section__meta">검색 관심도 z-score 기준 · 매일 새벽 갱신</span>
          </div>
          <p className="tier-section__caption">
            저장된 신조어를 최근 반응 세기 순으로 정렬했어요. 클릭하면 바로 결과가 나와요 — 이미 분석까지 끝나 있어요.
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
                  <span className="tier-row__word">{row.keyword}</span>
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

      {unranked.length > 0 && (
        <div className="unknown-section">
          <h2 className="tier-section__title tier-section__title--sub">막 등록됨 · 순위 집계 전</h2>
          <p className="tier-section__caption">
            방금 수집이 끝난 신조어예요. 다음 순위 갱신(내일 새벽)부터 위 목록에 합류해요.
          </p>
          <ul className="unknown-list">
            {unranked.map((row) => (
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

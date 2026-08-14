// 출처별 사용자 친화적 라벨 + 아이콘 매핑.
// tavily는 특정 사이트가 아니라 여러 블로그/뉴스를 긁어오는 검색 API라
// domain이 없다 — 이 경우 파비콘 대신 이모지만 쓴다(faviconUrl이 null 반환).
export const SOURCE_META = {
  tavily: { label: '웹 검색', domain: null, emoji: '🌐' },
  youtube: { label: '유튜브', domain: 'youtube.com', emoji: '📺' },
  namuwiki: { label: '나무위키', domain: 'namu.wiki', emoji: '📖' },
  natepann: { label: '네이트판', domain: 'pann.nate.com', emoji: '💬' },
  dcinside: { label: '디시인사이드', domain: 'dcinside.com', emoji: '💬' },
  todayhumor: { label: '오늘의유머', domain: 'todayhumor.co.kr', emoji: '😂' },
  duckduckgo: { label: 'DuckDuckGo 검색', domain: null, emoji: '🦆' },
}

const DEFAULT_META = { label: '기타 출처', domain: null, emoji: '🔗' }

export function faviconUrl(domain) {
  if (!domain) return null
  return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`
}

export function sourceMetaFor(source) {
  return SOURCE_META[source] || DEFAULT_META
}

// 결과에 담긴 출처는 title/url뿐이라(어떤 source 키였는지 없음), url의 도메인으로
// 역추적해 아이콘/라벨을 찾는다. 매칭되는 도메인이 없으면(예: tavily가 긁어온
// 외부 블로그) 기본값(기타 출처)으로 떨어진다.
export function sourceMetaForUrl(url) {
  if (!url) return DEFAULT_META
  let hostname
  try {
    hostname = new URL(url).hostname
  } catch {
    return DEFAULT_META
  }
  for (const meta of Object.values(SOURCE_META)) {
    if (meta.domain && hostname.includes(meta.domain)) {
      return meta
    }
  }
  return DEFAULT_META
}

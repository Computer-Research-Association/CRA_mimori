const BASE = '/api'

export const AVAILABLE_SOURCES = [
  'tavily', 'youtube', 'namuwiki', 'natepann', 'dcinside', 'todayhumor',
]

async function handleResponse(res) {
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.error || `요청 실패 (${res.status})`)
  }
  return data
}

export async function fetchKeywords() {
  const res = await fetch(`${BASE}/keywords`)
  const data = await handleResponse(res)
  return data.keywords
}

export async function fetchTrend(keyword) {
  const res = await fetch(`${BASE}/trend/${keyword}`)
  if (res.status === 404) return null
  return handleResponse(res)
}

export async function analyzeKeyword(keyword) {
  const res = await fetch(`${BASE}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword }),
  })
  return handleResponse(res)
}

export async function askRag(keyword, question, sources) {
  const res = await fetch(`${BASE}/rag`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword, question, sources }),
  })
  return handleResponse(res)
}

export async function requestCrawl(keyword) {
  const res = await fetch(`${BASE}/crawl-request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword }),
  })
  return handleResponse(res)
}

export async function fetchCrawlStatus(keyword) {
  const res = await fetch(`${BASE}/crawl-request/${keyword}`)
  return handleResponse(res)
}

const BASE = '/api'

export const AVAILABLE_SOURCES = [
  'tavily', 'youtube', 'namuwiki', 'natepann', 'dcinside', 'todayhumor',
]

async function safeFetch(...args) {
  let res
  try {
    res = await fetch(...args)
  } catch {
    throw new Error('서버에 연결할 수 없습니다')
  }
  return res
}

async function handleResponse(res) {
  let data
  try {
    data = await res.json()
  } catch {
    throw new Error(`요청 실패 (${res.status})`)
  }
  if (!res.ok) {
    throw new Error(data.error || `요청 실패 (${res.status})`)
  }
  return data
}

export async function fetchKeywords() {
  const res = await safeFetch(`${BASE}/keywords`)
  const data = await handleResponse(res)
  return data.keywords
}

export async function fetchTrend(keyword) {
  const res = await safeFetch(`${BASE}/trend/${keyword}`)
  if (res.status === 404) return null
  return handleResponse(res)
}

export async function submitAnalyzeRequest(keyword, sources) {
  const res = await safeFetch(`${BASE}/analyze-request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword, sources }),
  })
  return handleResponse(res)
}

export async function fetchAnalyzeStatus(jobId) {
  const res = await safeFetch(`${BASE}/analyze-request/${jobId}`)
  return handleResponse(res)
}

export async function requestCrawl(keyword) {
  const res = await safeFetch(`${BASE}/crawl-request`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword }),
  })
  return handleResponse(res)
}

export async function fetchCrawlStatus(keyword) {
  const res = await safeFetch(`${BASE}/crawl-request/${keyword}`)
  return handleResponse(res)
}

export async function fetchHiddenKeywords() {
  const res = await safeFetch(`${BASE}/keywords/hidden`)
  const data = await handleResponse(res)
  return data.keywords
}

export async function hideKeyword(keyword) {
  const res = await safeFetch(`${BASE}/keywords/${keyword}/hide`, { method: 'POST' })
  return handleResponse(res)
}

export async function unhideKeyword(keyword) {
  const res = await safeFetch(`${BASE}/keywords/${keyword}/unhide`, { method: 'POST' })
  return handleResponse(res)
}

export async function deleteKeywordPermanently(keyword) {
  const res = await safeFetch(`${BASE}/keywords/${keyword}`, { method: 'DELETE' })
  return handleResponse(res)
}

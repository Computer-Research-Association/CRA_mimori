const BASE = '/api'
const ADMIN_KEY_STORAGE = 'mimori_admin_key'

export const AVAILABLE_SOURCES = [
  'tavily', 'youtube', 'namuwiki', 'natepann', 'dcinside', 'todayhumor',
]

// 관리자 전용 엔드포인트(hide/unhide/완전삭제/admin stats)에 실어 보낼 키.
// AdminPage에서 입력받아 setAdminKey로 저장한다 — 페이지를 새로고침해도
// 다시 입력하지 않도록 localStorage에도 함께 둔다.
// import 시점이 아니라 최초 호출 시점에 읽고, localStorage 자체가 없는 환경(테스트,
// 저장소가 막힌 브라우저 등)에서도 죽지 않도록 존재 여부를 매번 확인한다.
let adminKey = null

function loadAdminKey() {
  if (adminKey === null) {
    adminKey = (typeof localStorage !== 'undefined' && localStorage.getItem(ADMIN_KEY_STORAGE)) || ''
  }
  return adminKey
}

export function getAdminKey() {
  return loadAdminKey()
}

export function setAdminKey(key) {
  adminKey = key
  if (typeof localStorage !== 'undefined') localStorage.setItem(ADMIN_KEY_STORAGE, key)
}

function adminHeaders() {
  return { 'X-Admin-Key': loadAdminKey() }
}

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

export async function fetchTrendLeaderboard() {
  const res = await safeFetch(`${BASE}/trend`)
  const data = await handleResponse(res)
  return data.keywords
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

export async function fetchAdminStats() {
  const res = await safeFetch(`${BASE}/admin/stats`, { headers: adminHeaders() })
  return handleResponse(res)
}

export async function fetchHiddenKeywords() {
  const res = await safeFetch(`${BASE}/keywords/hidden`, { headers: adminHeaders() })
  const data = await handleResponse(res)
  return data.keywords
}

export async function hideKeyword(keyword) {
  const res = await safeFetch(`${BASE}/keywords/${keyword}/hide`, { method: 'POST', headers: adminHeaders() })
  return handleResponse(res)
}

export async function unhideKeyword(keyword) {
  const res = await safeFetch(`${BASE}/keywords/${keyword}/unhide`, { method: 'POST', headers: adminHeaders() })
  return handleResponse(res)
}

export async function deleteKeywordPermanently(keyword) {
  const res = await safeFetch(`${BASE}/keywords/${keyword}`, { method: 'DELETE', headers: adminHeaders() })
  return handleResponse(res)
}

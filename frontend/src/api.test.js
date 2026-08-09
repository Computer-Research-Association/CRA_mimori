import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchKeywords, fetchTrend, analyzeKeyword, askRag, requestCrawl, fetchCrawlStatus, AVAILABLE_SOURCES } from './api.js'

beforeEach(() => {
  global.fetch = vi.fn()
})

function jsonResponse(body, ok = true, status = ok ? 200 : 400) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
  })
}

describe('AVAILABLE_SOURCES', () => {
  it('6개 소스를 포함한다', () => {
    expect(AVAILABLE_SOURCES).toEqual([
      'tavily', 'youtube', 'namuwiki', 'natepann', 'dcinside', 'todayhumor',
    ])
  })
})

describe('fetchKeywords', () => {
  it('/api/keywords를 호출하고 목록을 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keywords: ['야르', '쌰갈'] }))
    const result = await fetchKeywords()
    expect(fetch).toHaveBeenCalledWith('/api/keywords')
    expect(result).toEqual(['야르', '쌰갈'])
  })
})

describe('fetchTrend', () => {
  it('404면 null을 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({ error: '없음' }, false, 404))
    const result = await fetchTrend('없는키워드')
    expect(result).toBeNull()
  })

  it('있으면 트렌드 객체를 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({ status: '유행 중', final_z: 1.2 }))
    const result = await fetchTrend('야르')
    expect(fetch).toHaveBeenCalledWith('/api/trend/야르')
    expect(result.status).toBe('유행 중')
  })
})

describe('analyzeKeyword', () => {
  it('POST /api/analyze를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ result: '분석결과', trend: null }))
    const result = await analyzeKeyword('야르')
    expect(fetch).toHaveBeenCalledWith('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '야르' }),
    })
    expect(result.result).toBe('분석결과')
  })

  it('실패 응답이면 에러 메시지를 담은 Error를 던진다', async () => {
    fetch.mockReturnValue(jsonResponse({ error: '데이터를 찾을 수 없습니다' }, false, 404))
    await expect(analyzeKeyword('없는키워드')).rejects.toThrow('데이터를 찾을 수 없습니다')
  })
})

describe('askRag', () => {
  it('sources를 포함해서 POST /api/rag를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ answer: '답변', sources: [], trend: null }))
    await askRag('야르', '무슨 뜻이야?', ['tavily'])
    expect(fetch).toHaveBeenCalledWith('/api/rag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '야르', question: '무슨 뜻이야?', sources: ['tavily'] }),
    })
  })
})

describe('requestCrawl', () => {
  it('POST /api/crawl-request를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keyword: '흘로망', status: 'queued' }))
    const result = await requestCrawl('흘로망')
    expect(fetch).toHaveBeenCalledWith('/api/crawl-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '흘로망' }),
    })
    expect(result.status).toBe('queued')
  })
})

describe('fetchCrawlStatus', () => {
  it('GET /api/crawl-request/<keyword>를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keyword: '흘로망', status: 'running' }))
    const result = await fetchCrawlStatus('흘로망')
    expect(fetch).toHaveBeenCalledWith('/api/crawl-request/흘로망')
    expect(result.status).toBe('running')
  })
})

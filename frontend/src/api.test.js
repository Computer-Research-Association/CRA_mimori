import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fetchKeywords, fetchTrend, fetchTrendLeaderboard, fetchAdminStats, requestCrawl, fetchCrawlStatus,
  AVAILABLE_SOURCES, submitAnalyzeRequest, fetchAnalyzeStatus,
} from './api.js'

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

describe('fetchAdminStats', () => {
  it('/api/admin/stats를 호출하고 결과를 그대로 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({ keyword_count: 22, keyword_cap: 60 }))
    const result = await fetchAdminStats()
    expect(fetch).toHaveBeenCalledWith('/api/admin/stats')
    expect(result).toEqual({ keyword_count: 22, keyword_cap: 60 })
  })
})

describe('fetchTrendLeaderboard', () => {
  it('/api/trend를 호출하고 키워드 배열을 반환한다', async () => {
    fetch.mockReturnValue(jsonResponse({
      keywords: [{ keyword: '야르', status: '핫함', z_score: 2.1, final_z: 2.1 }],
    }))
    const result = await fetchTrendLeaderboard()
    expect(fetch).toHaveBeenCalledWith('/api/trend')
    expect(result).toEqual([{ keyword: '야르', status: '핫함', z_score: 2.1, final_z: 2.1 }])
  })
})

describe('submitAnalyzeRequest', () => {
  it('sources를 포함해서 POST /api/analyze-request를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({ job_id: '잡아이디', status: 'queued' }))
    const result = await submitAnalyzeRequest('야르', ['tavily', 'youtube'])
    expect(fetch).toHaveBeenCalledWith('/api/analyze-request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: '야르', sources: ['tavily', 'youtube'] }),
    })
    expect(result.status).toBe('queued')
  })

  it('실패 응답이면 에러 메시지를 담은 Error를 던진다', async () => {
    fetch.mockReturnValue(jsonResponse({ error: '데이터를 찾을 수 없습니다' }, false, 404))
    await expect(submitAnalyzeRequest('없는키워드', ['tavily'])).rejects.toThrow('데이터를 찾을 수 없습니다')
  })
})

describe('fetchAnalyzeStatus', () => {
  it('GET /api/analyze-request/<job_id>를 호출한다', async () => {
    fetch.mockReturnValue(jsonResponse({
      job_id: '잡아이디', keyword: '야르', status: 'done', result: '분석결과', sources: [], trend: null, error: null,
    }))
    const result = await fetchAnalyzeStatus('잡아이디')
    expect(fetch).toHaveBeenCalledWith('/api/analyze-request/잡아이디')
    expect(result.status).toBe('done')
    expect(result.result).toBe('분석결과')
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

describe('handleResponse의 에러 처리', () => {
  it('응답 본문이 JSON이 아니면 상태 코드를 담은 대체 메시지를 던진다', async () => {
    fetch.mockReturnValue(Promise.resolve({
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error('bad json')),
    }))

    await expect(fetchKeywords()).rejects.toThrow('요청 실패 (502)')
  })

  it('fetch 자체가 실패하면(네트워크 오류) 한국어 메시지를 던진다', async () => {
    fetch.mockRejectedValue(new Error('network down'))

    await expect(fetchKeywords()).rejects.toThrow('서버에 연결할 수 없습니다')
  })
})

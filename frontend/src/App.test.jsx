import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import App from './App.jsx'
import * as api from './api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App', () => {
  it('시작 시 키워드 목록을 불러와서 KeywordSelector에 전달한다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    render(<App />)
    expect(await screen.findByRole('combobox')).toBeInTheDocument()
  })

  it('키워드 목록이 있으면 예시 키워드 칩을 보여준다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르', '쌰갈'])
    render(<App />)
    expect(await screen.findByRole('button', { name: '야르' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '쌰갈' })).toBeInTheDocument()
  })

  it('키워드를 선택하면 예시 키워드 칩이 사라진다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      keyword: '야르', status: 'done', result: '야르 분석 결과', sources: [], trend: null, error: null,
    })

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: '야르' }))

    expect(await screen.findByText('야르 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '야르' })).not.toBeInTheDocument()
  }, 6000)

  it('기존 키워드를 검색하면 AnalysisPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      keyword: '야르', status: 'done', result: '야르 분석 결과', sources: [], trend: null, error: null,
    })

    render(<App />)
    const input = await screen.findByRole('combobox')
    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('야르 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()
  }, 6000)

  it('없는 키워드를 검색하면 CrawlRequestPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue([])
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({ keyword: '흘로망', status: 'queued' })

    render(<App />)
    const input = await screen.findByRole('combobox')
    await userEvent.type(input, '흘로망')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText(/수집 중입니다/)).toBeInTheDocument()
  })

  it('키워드 목록을 불러오지 못하면 에러 메시지를 보여준다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockRejectedValue(new Error('네트워크 오류'))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('네트워크 오류')
  })

  it('키워드를 전환하면 이전 키워드의 RAG 답변이 더 이상 보이지 않는다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르', '쌰갈'])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'submitAnalyzeRequest').mockImplementation((keyword) =>
      Promise.resolve({ keyword, status: 'queued' }),
    )
    vi.spyOn(api, 'fetchAnalyzeStatus').mockImplementation((keyword) =>
      Promise.resolve({
        keyword, status: 'done', result: `${keyword} 분석 결과`, sources: [], trend: null, error: null,
      }),
    )
    vi.spyOn(api, 'submitRagRequest').mockImplementation((keyword) =>
      Promise.resolve({ job_id: `job-${keyword}`, status: 'queued' }),
    )
    vi.spyOn(api, 'fetchRagStatus').mockImplementation((jobId) =>
      Promise.resolve({
        job_id: jobId, status: 'done', answer: `${jobId.replace('job-', '')} RAG 답변`, sources: [], trend: null, error: null,
      }),
    )

    render(<App />)
    const input = await screen.findByRole('combobox')

    // 키워드 A(야르) 검색 후 질문
    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))
    expect(await screen.findByText('야르 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()

    await userEvent.type(screen.getByRole('textbox', { name: '질문' }), '무슨 뜻이야?')
    await userEvent.click(screen.getByRole('button', { name: '질문하기' }))
    expect(await screen.findByText('야르 RAG 답변', {}, { timeout: 4000 })).toBeInTheDocument()

    // 키워드 B(쌰갈)로 전환
    await userEvent.clear(input)
    await userEvent.type(input, '쌰갈')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('쌰갈 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.queryByText('야르 RAG 답변')).not.toBeInTheDocument()
  }, 15000)
})

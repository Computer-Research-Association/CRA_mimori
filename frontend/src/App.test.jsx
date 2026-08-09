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
    expect(await screen.findByRole('textbox')).toBeInTheDocument()
  })

  it('기존 키워드를 검색하면 AnalysisPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({ result: '야르 분석 결과', trend: null })

    render(<App />)
    const input = await screen.findByRole('textbox')
    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('야르 분석 결과')).toBeInTheDocument()
  })

  it('없는 키워드를 검색하면 CrawlRequestPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue([])
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({ keyword: '흘로망', status: 'queued' })

    render(<App />)
    const input = await screen.findByRole('textbox')
    await userEvent.type(input, '흘로망')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText(/수집 중입니다/)).toBeInTheDocument()
  })
})

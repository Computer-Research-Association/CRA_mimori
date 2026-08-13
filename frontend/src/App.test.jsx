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

  it('기존 키워드를 검색하면 AnalysisPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({ result: '야르 분석 결과', trend: null })

    render(<App />)
    const input = await screen.findByRole('combobox')
    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('야르 분석 결과')).toBeInTheDocument()
  })

  it('없는 키워드를 검색하면 CrawlRequestPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue([])
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({ keyword: '흘로망', status: 'queued' })

    render(<App />)
    const input = await screen.findByRole('combobox')
    await userEvent.type(input, '흘로망')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    // 큐에 들어갔지만 워커가 아직 안 집은 상태의 문구.
    // (단계가 잡히면 '웹에서 자료 수집 중' 등으로 바뀐다 — CrawlRequestPanel.test.jsx 참고)
    expect(await screen.findByText(/순서를 기다리는 중/)).toBeInTheDocument()
  })

  it('키워드 목록을 불러오지 못하면 에러 메시지를 보여준다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockRejectedValue(new Error('네트워크 오류'))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('네트워크 오류')
  })

  it('키워드를 전환하면 이전 키워드의 RAG 답변이 더 이상 보이지 않는다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르', '쌰갈'])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockImplementation((keyword) =>
      Promise.resolve({ result: `${keyword} 분석 결과`, trend: null }),
    )
    vi.spyOn(api, 'askRag').mockImplementation((keyword) =>
      Promise.resolve({ answer: `${keyword} RAG 답변`, sources: [], trend: null }),
    )

    render(<App />)
    const input = await screen.findByRole('combobox')

    // 키워드 A(야르) 검색 후 질문
    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))
    expect(await screen.findByText('야르 분석 결과')).toBeInTheDocument()

    await userEvent.type(screen.getByRole('textbox', { name: '질문' }), '무슨 뜻이야?')
    await userEvent.click(screen.getByRole('button', { name: '질문하기' }))
    expect(await screen.findByText('야르 RAG 답변')).toBeInTheDocument()

    // 키워드 B(쌰갈)로 전환 — 홈 화면이 사라지고 결과 화면의 compact input으로 바뀜
    const compactInput = screen.getByRole('combobox')
    await userEvent.clear(compactInput)
    await userEvent.type(compactInput, '쌰갈')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('쌰갈 분석 결과')).toBeInTheDocument()
    expect(screen.queryByText('야르 RAG 답변')).not.toBeInTheDocument()
  })
})

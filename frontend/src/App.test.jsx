import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import App from './App.jsx'
import * as api from './api.js'
import { sourceMetaFor } from './sourceMeta.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App', () => {
  it('시작 시 키워드 목록을 불러와서 KeywordSelector에 전달한다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
    render(<App />)
    expect(await screen.findByRole('combobox')).toBeInTheDocument()
  })

  it('홈 화면에서도 관리자 화면 링크를 바로 볼 수 있다(키워드 선택 없이도)', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
    render(<App />)
    const link = await screen.findByRole('link', { name: '관리자 화면' })
    expect(link).toHaveAttribute('href', '/admin')
  })

  it('순위표에 순위/상태/z-score가 있는 키워드를 클릭하면 결과 화면으로 전환된다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르', '쌰갈'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { keyword: '야르', status: '핫함', z_score: 2.1, final_z: 2.1 },
      { keyword: '쌰갈', status: '평상', z_score: 0.1, final_z: 0.1 },
    ])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'done', result: '야르 분석 결과', sources: [], trend: null, error: null,
    })

    render(<App />)
    expect(await screen.findByText('핫함')).toBeInTheDocument()
    await userEvent.click(screen.getByText('야르'))

    expect(await screen.findByText('야르 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.queryByText('핫함')).not.toBeInTheDocument()
  }, 6000)

  it('아직 순위 집계 전인 키워드는 별도 그룹에 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['막등록됨'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { keyword: '막등록됨', status: null, z_score: null, final_z: null },
    ])
    render(<App />)
    expect(await screen.findByText('막 등록됨 · 순위 집계 전')).toBeInTheDocument()
    expect(screen.getByText('막등록됨')).toBeInTheDocument()
  })

  it('출처 선택 체크박스가 기본적으로 전부 켜진 상태로 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
    render(<App />)
    for (const source of api.AVAILABLE_SOURCES) {
      expect(await screen.findByLabelText(sourceMetaFor(source).label)).toBeChecked()
    }
  })

  it('출처를 전부 해제하면 검색 버튼이 비활성화된다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
    render(<App />)
    await screen.findByRole('combobox')
    for (const source of api.AVAILABLE_SOURCES) {
      await userEvent.click(screen.getByLabelText(sourceMetaFor(source).label))
    }
    expect(screen.getByRole('button', { name: '검색' })).toBeDisabled()
    expect(screen.getByText('최소 하나의 출처를 선택하세요')).toBeInTheDocument()
  })

  it('기존 키워드를 검색하면 선택된 출처와 함께 분석 요청을 보내고 결과를 보여준다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    const submitSpy = vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'done', result: '야르 분석 결과', sources: [], trend: null, error: null,
    })

    render(<App />)
    const input = await screen.findByRole('combobox')
    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('야르 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(submitSpy).toHaveBeenCalledWith('야르', api.AVAILABLE_SOURCES)
  }, 6000)

  it('없는 키워드를 검색하면 CrawlRequestPanel이 뜬다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue([])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
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
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('네트워크 오류')
  })

  it('키워드를 전환하면 이전 키워드의 분석 결과가 더 이상 보이지 않는다', async () => {
    vi.spyOn(api, 'fetchKeywords').mockResolvedValue(['야르', '쌰갈'])
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'submitAnalyzeRequest').mockImplementation((keyword) =>
      Promise.resolve({ job_id: `job-${keyword}`, status: 'queued' }),
    )
    vi.spyOn(api, 'fetchAnalyzeStatus').mockImplementation((jobId) =>
      Promise.resolve({
        job_id: jobId, status: 'done', result: `${jobId.replace('job-', '')} 분석 결과`, sources: [], trend: null, error: null,
      }),
    )

    render(<App />)
    const input = await screen.findByRole('combobox')

    await userEvent.type(input, '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))
    expect(await screen.findByText('야르 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()

    // 키워드 B(쌰갈)로 전환 — 홈 화면이 사라지고 결과 화면의 compact input으로 바뀜
    const compactInput = screen.getByRole('combobox')
    await userEvent.clear(compactInput)
    await userEvent.type(compactInput, '쌰갈')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByText('쌰갈 분석 결과', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.queryByText('야르 분석 결과')).not.toBeInTheDocument()
  }, 10000)
})

import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import AnalysisPanel from './AnalysisPanel.jsx'
import * as api from '../api.js'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('AnalysisPanel', () => {
  it('마운트되면 선택된 출처와 함께 submitAnalyzeRequest와 fetchTrend를 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)

    render(<AnalysisPanel keyword="야르" selectedSources={['tavily', 'youtube']} />)

    await waitFor(() => expect(submitSpy).toHaveBeenCalledWith('야르', ['tavily', 'youtube']))
  })

  it('완료된 긴 답변은 타이핑 대기 없이 전체를 즉시 보여준다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'done' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    const text = '완료된 분석을 기다리지 않고 읽습니다. '.repeat(100)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'done', result: text, sources: [], error: null,
    })
    const { container } = render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)
    await waitFor(() => expect(container.querySelector('.markdown-body')?.textContent).toContain(text.trim()))
    expect(container.querySelector('.markdown-body--typing')).toBeNull()
  })

  it('캐시된(이미 done인) 결과는 첫 폴링 주기(3초)를 기다리지 않고 바로 보인다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'done' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'done', result: '# 캐시된 결과',
      sources: [{ title: '캐시 출처', url: 'https://example.com' }], trend: null, error: null,
    })

    render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)

    // 타이머를 전혀 진행시키지 않아도(첫 폴링 주기 전에도) 바로 나와야 한다.
    expect(await screen.findByText('캐시 출처')).toBeInTheDocument()
  })

  it('status가 done이 되면 결과와 출처를 렌더링하고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue({ status: '유행 중', final_z: 1.2 })
    const fetchStatusSpy = vi.spyOn(api, 'fetchAnalyzeStatus')
      .mockResolvedValueOnce({ job_id: '잡아이디', status: 'running' })
      .mockResolvedValueOnce({
        job_id: '잡아이디', status: 'done', result: '# 분석 결과',
        sources: [{ title: '제목', url: 'https://example.com' }], trend: null, error: null,
      })

    render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)

    await vi.advanceTimersByTimeAsync(3000)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText('제목')).toBeInTheDocument()
    const callsAfterDone = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterDone)
  })

  it('running 상태에서 partial_result가 오면 로딩 문구 대신 진행 중인 답변을 렌더링한다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'running',
      sources: [{ title: '제목', url: 'https://example.com' }],
      partial_result: '지금까지 생성된 답변',
    })

    render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText('지금까지 생성된 답변')).toBeInTheDocument()
    expect(screen.getByText('작성 중...')).toBeInTheDocument()
    expect(screen.queryByText(/관련 커뮤니티 자료를 찾는 중/)).not.toBeInTheDocument()
  })

  it('low_confidence가 true면 확신도 낮음 안내가 뜨고, false/누락이면 안 뜬다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValueOnce({
      job_id: '잡아이디', status: 'done', result: '# 분석 결과',
      sources: [{ title: '제목', url: 'https://example.com' }], trend: null,
      low_confidence: true, error: null,
    })

    render(<AnalysisPanel keyword="ㅈㄱㄴ" selectedSources={['tavily']} />)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText(/확신도가 낮아요/)).toBeInTheDocument()
  })

  it('분석이 아직 done이 아니어도 트렌드는 먼저 보여준다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue({ status: '유행 중', final_z: 1.2 })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({ job_id: '잡아이디', status: 'running' })

    render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)

    expect(await screen.findByText(/트렌드: 유행 중/)).toBeInTheDocument()
  })

  it('status가 failed면 에러와 재시도 버튼을 보여준다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'failed', error: '분석 실패했습니다', result: null, sources: null, trend: null,
    })

    render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText(/분석 실패했습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })

  it('재시도 버튼을 누르면 submitAnalyzeRequest를 다시 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'failed', error: '분석 실패했습니다', result: null, sources: null, trend: null,
    })

    render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)
    await vi.advanceTimersByTimeAsync(3000)
    await screen.findByText(/분석 실패했습니다/)

    screen.getByRole('button', { name: '다시 시도' }).click()
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2))
  })
})

it('분석 본문을 기다리는 동안에도 확인 가능한 근거 자료를 보여준다', async () => {
  vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: 'job', status: 'queued' })
  vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
  vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({ status: 'running', sources: [{ title: '원문', url: 'https://example.com/a' }] })
  render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)
  expect(await screen.findByRole('link', { name: '[자료 1] 원문' })).toHaveAttribute('href', 'https://example.com/a')
  expect(screen.getByText('근거 자료 1개')).toBeInTheDocument()
})

it('본문 인용은 실제 출처 항목으로 연결되고 누락 경고를 표시한다', async () => {
  vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: 'job', status: 'done' })
  vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
  vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
    status: 'done', result: '설명 [자료 1](#source-1)', citation_warning: true,
    sources: [{ title: '원문', url: 'https://example.com/a' }],
  })
  const { container } = render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)
  expect(await screen.findByRole('link', { name: '자료 1' })).toHaveAttribute('href', '#source-1')
  expect(container.querySelector('#source-1')).toHaveTextContent('원문')
  expect(screen.getByText(/일부 설명의 출처를 확인하지 못했어요/)).toBeInTheDocument()
})

it('이전 키워드의 느린 응답이 현재 키워드의 분석을 덮지 않는다', async () => {
  let resolveOld
  const oldRequest = new Promise((resolve) => { resolveOld = resolve })
  vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
  vi.spyOn(api, 'submitAnalyzeRequest').mockImplementation((keyword) => keyword === '이전' ? oldRequest : Promise.resolve({ job_id: 'new', status: 'done' }))
  const status = vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({ status: 'done', result: '현재 분석', sources: [] })
  const sources = ['tavily']
  const { rerender } = render(<AnalysisPanel keyword="이전" selectedSources={sources} />)
  rerender(<AnalysisPanel keyword="현재" selectedSources={sources} />)
  await screen.findByText('현재 분석')
  resolveOld({ job_id: 'old', status: 'done' })
  await Promise.resolve()
  expect(status).not.toHaveBeenCalledWith('old')
})

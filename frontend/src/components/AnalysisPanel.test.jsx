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

  it('답변이 즉시 통째로 나오지 않고 한 글자씩 늘어나며(타이핑 커서 포함) 결국 전체가 보인다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ job_id: '잡아이디', status: 'done' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'done', result: '충분히 긴 테스트용 답변 문장입니다',
      sources: [], trend: null, error: null,
    })

    const { container } = render(<AnalysisPanel keyword="야르" selectedSources={['tavily']} />)

    // 타이핑 커서가 붙어있어야 "지금 쓰이는 중"임을 알 수 있다.
    await waitFor(() => {
      expect(container.querySelector('.markdown-body--typing')).toBeTruthy()
    })

    // 전체 문장이 끝까지 다 보일 때까지 기다리면, 커서도 사라진다.
    await waitFor(() => {
      expect(screen.getByText('충분히 긴 테스트용 답변 문장입니다', { exact: false })).toBeInTheDocument()
    })
    await waitFor(() => {
      expect(container.querySelector('.markdown-body--typing')).toBeFalsy()
    })
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

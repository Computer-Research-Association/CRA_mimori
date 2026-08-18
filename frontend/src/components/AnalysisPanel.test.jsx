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

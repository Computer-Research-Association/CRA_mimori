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
  it('마운트되면 submitAnalyzeRequest와 fetchTrend를 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)

    render(<AnalysisPanel keyword="야르" />)

    await waitFor(() => expect(submitSpy).toHaveBeenCalledWith('야르'))
  })

  it('status가 done이 되면 결과와 출처를 렌더링하고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue({ status: '유행 중', final_z: 1.2 })
    const fetchStatusSpy = vi.spyOn(api, 'fetchAnalyzeStatus')
      .mockResolvedValueOnce({ keyword: '야르', status: 'running' })
      .mockResolvedValueOnce({
        keyword: '야르', status: 'done', result: '# 분석 결과',
        sources: [{ title: '제목', url: 'https://example.com' }], trend: null, error: null,
      })

    render(<AnalysisPanel keyword="야르" />)

    await vi.advanceTimersByTimeAsync(3000)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText('제목')).toBeInTheDocument()
    const callsAfterDone = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterDone)
  })

  it('status가 failed면 에러와 재시도 버튼을 보여준다', async () => {
    vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      keyword: '야르', status: 'failed', error: '분석 실패했습니다', result: null, sources: null, trend: null,
    })

    render(<AnalysisPanel keyword="야르" />)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText(/분석 실패했습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })

  it('재시도 버튼을 누르면 submitAnalyzeRequest를 다시 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitAnalyzeRequest').mockResolvedValue({ keyword: '야르', status: 'queued' })
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'fetchAnalyzeStatus').mockResolvedValue({
      keyword: '야르', status: 'failed', error: '분석 실패했습니다', result: null, sources: null, trend: null,
    })

    render(<AnalysisPanel keyword="야르" />)
    await vi.advanceTimersByTimeAsync(3000)
    await screen.findByText(/분석 실패했습니다/)

    screen.getByRole('button', { name: '다시 시도' }).click()
    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(2))
  })
})

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import AnalysisPanel from './AnalysisPanel.jsx'
import * as api from '../api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AnalysisPanel', () => {
  it('로딩 중에는 로딩 표시를 보여준다', () => {
    vi.spyOn(api, 'fetchTrend').mockReturnValue(new Promise(() => {}))
    vi.spyOn(api, 'analyzeKeyword').mockReturnValue(new Promise(() => {}))
    render(<AnalysisPanel keyword="야르" />)
    expect(screen.getByText(/분석 중/)).toBeInTheDocument()
  })

  it('성공하면 분석 결과와 트렌드를 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue({ status: '유행 중', final_z: 1.2 })
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({ result: '야르는 ~라는 뜻입니다', trend: null })

    render(<AnalysisPanel keyword="야르" />)

    expect(await screen.findByText('야르는 ~라는 뜻입니다')).toBeInTheDocument()
    expect(screen.getByText(/유행 중/)).toBeInTheDocument()
  })

  it('출처가 있으면 출처 목록을 링크로 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({
      result: '분석 결과',
      sources: [{ title: '문서 제목', url: 'https://example.com/a' }],
      trend: null,
    })

    render(<AnalysisPanel keyword="야르" />)

    const link = await screen.findByRole('link', { name: '문서 제목' })
    expect(link).toHaveAttribute('href', 'https://example.com/a')
  })

  it('트렌드 데이터가 없으면(null) 트렌드 표시 없이 분석 결과만 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockResolvedValue({ result: '분석 결과', trend: null })

    render(<AnalysisPanel keyword="야르" />)

    expect(await screen.findByText('분석 결과')).toBeInTheDocument()
    expect(screen.queryByText(/유행/)).not.toBeInTheDocument()
  })

  it('분석 실패시 에러 메시지를 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    vi.spyOn(api, 'analyzeKeyword').mockRejectedValue(new Error('데이터를 찾을 수 없습니다'))

    render(<AnalysisPanel keyword="야르" />)

    expect(await screen.findByText('데이터를 찾을 수 없습니다')).toBeInTheDocument()
  })

  it('실패 후 다시 시도를 누르면 재요청하여 성공 시 결과를 보여준다', async () => {
    vi.spyOn(api, 'fetchTrend').mockResolvedValue(null)
    const analyzeSpy = vi.spyOn(api, 'analyzeKeyword')
      .mockRejectedValueOnce(new Error('일시적 오류'))
      .mockResolvedValueOnce({ result: '재시도 성공 결과', trend: null })

    render(<AnalysisPanel keyword="야르" />)

    expect(await screen.findByText('일시적 오류')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '다시 시도' }))

    expect(await screen.findByText('재시도 성공 결과')).toBeInTheDocument()
    expect(analyzeSpy).toHaveBeenCalledTimes(2)
  })
})

import { render, screen } from '@testing-library/react'
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
})

import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import TrendGauge from './TrendGauge.jsx'

describe('TrendGauge', () => {
  it('trend가 없으면 아무것도 렌더링하지 않는다', () => {
    const { container } = render(<TrendGauge trend={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('데이터 부족이면 아무것도 렌더링하지 않는다', () => {
    const { container } = render(<TrendGauge trend={{ status: '데이터 부족' }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('z-score가 있으면 게이지와 설명 캡션을 보여준다', () => {
    render(<TrendGauge trend={{ status: '유행 중', final_z: 1.2 }} />)
    expect(screen.getByText('+1.20')).toBeInTheDocument()
    expect(screen.getByText('최근 언급량을 평소와 비교해 보여줘요')).toBeInTheDocument()
  })
})

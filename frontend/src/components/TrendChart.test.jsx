import { render } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import TrendChart from './TrendChart.jsx'

describe('TrendChart', () => {
  it('점이 2개 미만이면 아무것도 렌더링하지 않는다', () => {
    const { container } = render(<TrendChart points={[{ date: '2026-08-01', ratio: 10 }]} label="관심도" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('평균선을 실제 값 평균 위치에 그린다', () => {
    const points = [
      { date: '2026-08-01', ratio: 0 },
      { date: '2026-08-02', ratio: 50 },
      { date: '2026-08-03', ratio: 100 },
    ]
    const { container } = render(<TrendChart points={points} label="관심도" />)
    const svgHeight = Number(container.querySelector('svg').getAttribute('viewBox').split(' ')[3])
    const mean = container.querySelector('.trend-chart__mean')
    // min=0, max=100이므로 평균(50)은 세로 중앙(min과 max의 정중앙) 근처에 와야 한다
    // (viewBox 높이 자체는 UI 크기 조정으로 바뀔 수 있어 하드코딩하지 않고 상대적으로 확인).
    expect(mean).toHaveAttribute('y1', mean.getAttribute('y2'))
    const y = Number(mean.getAttribute('y1'))
    expect(y).toBeGreaterThan(svgHeight * 0.3)
    expect(y).toBeLessThan(svgHeight * 0.7)
  })

  it('aria-label에 평균선이라는 설명이 포함된다', () => {
    const points = [
      { date: '2026-08-01', ratio: 10 },
      { date: '2026-08-02', ratio: 20 },
    ]
    const { container } = render(<TrendChart points={points} label="관심도" />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-label', expect.stringContaining('평균값'))
  })

  it('충분히 튀는 꺾이는 지점에는 날짜 라벨이 붙는다', () => {
    // 08-03이 이웃(0, 0) 대비 뚜렷하게 튀는 피크(스팬의 15% 이상).
    const points = [
      { date: '2026-08-01', ratio: 0 },
      { date: '2026-08-02', ratio: 0 },
      { date: '2026-08-03', ratio: 100 },
      { date: '2026-08-04', ratio: 0 },
      { date: '2026-08-05', ratio: 0 },
    ]
    const { container } = render(<TrendChart points={points} label="관심도" />)
    expect(container.querySelector('.trend-chart__point-label')).toHaveTextContent('08/03')
    expect(container.querySelector('.trend-chart__point')).toBeInTheDocument()
  })

  it('사소한 잡음(전체 스팬 대비 미미한 등락)에는 라벨을 붙이지 않는다', () => {
    // 08-03에 진짜 큰 피크(스팬=100)가 있고, 나머지는 0~1 사이의 미세한 잡음뿐.
    // 잡음의 돌출은 스팬(100)의 15%에 한참 못 미치므로 진짜 피크 하나만 라벨이 붙어야 한다.
    const points = [
      { date: '2026-08-01', ratio: 0 },
      { date: '2026-08-02', ratio: 1 },
      { date: '2026-08-03', ratio: 100 },
      { date: '2026-08-04', ratio: 0 },
      { date: '2026-08-05', ratio: 1 },
      { date: '2026-08-06', ratio: 0 },
    ]
    const { container } = render(<TrendChart points={points} label="관심도" />)
    const labels = container.querySelectorAll('.trend-chart__point-label')
    expect(labels.length).toBe(1)
    expect(labels[0]).toHaveTextContent('08/03')
  })

  it('꺾이는 지점이 많아도 최대 6개까지만(돌출이 큰 순) 표시한다', () => {
    const points = Array.from({ length: 30 }, (_, i) => ({
      date: `2026-08-${String(i + 1).padStart(2, '0')}`,
      ratio: i % 2 === 0 ? 0 : 100, // 지그재그 — 사실상 모든 중간 지점이 피크/밸리
    }))
    const { container } = render(<TrendChart points={points} label="관심도" />)
    expect(container.querySelectorAll('.trend-chart__point-label').length).toBeLessThanOrEqual(6)
  })

  it('선은 값 범위 기반 그라디언트(시안=최저 → 레드=최고)로 칠해진다', () => {
    const points = [
      { date: '2026-08-01', ratio: 0 },
      { date: '2026-08-02', ratio: 100 },
    ]
    const { container } = render(<TrendChart points={points} label="관심도" />)
    const gradient = container.querySelector('linearGradient')
    expect(gradient).toBeInTheDocument()
    const stops = gradient.querySelectorAll('stop')
    expect(stops[0]).toHaveAttribute('stop-color', 'rgb(0, 240, 255)') // 0% = 시안(최저)
    expect(stops[stops.length - 1]).toHaveAttribute('stop-color', 'rgb(255, 104, 0)') // 100% = 레드(최고)

    const line = container.querySelector('.trend-chart__line')
    expect(line.getAttribute('stroke')).toMatch(/^url\(#/)
  })

  it('점 마커는 그 지점 값의 상대 위치에 맞는 그라디언트 색을 인라인으로 받는다', () => {
    const points = [
      { date: '2026-08-01', ratio: 0 },
      { date: '2026-08-02', ratio: 0 },
      { date: '2026-08-03', ratio: 100 }, // 최고값 피크 -> 레드에 가까워야 함
      { date: '2026-08-04', ratio: 0 },
      { date: '2026-08-05', ratio: 0 },
    ]
    const { container } = render(<TrendChart points={points} label="관심도" />)
    const marker = container.querySelector('.trend-chart__point')
    expect(marker.style.backgroundColor).toBe('rgb(255, 104, 0)')
  })

  it('핫함/유행 중/평상/감소 근사 기준선 중 이 구간 값 범위 안에 드는 것만 그려진다', () => {
    // 0~100을 10 간격으로 11개 찍으면 mean=50, std≈31.76.
    // z=0.5(→65.9)/-0.5(→34.1)는 0~100 범위 안, z=2(→113.5)/-2(→-13.5)는 범위 밖.
    const points = Array.from({ length: 11 }, (_, i) => ({
      date: `2026-08-${String(i + 1).padStart(2, '0')}`,
      ratio: i * 10,
    }))
    const { container } = render(<TrendChart points={points} label="관심도" />)
    const labels = [...container.querySelectorAll('.trend-chart__tier-label')].map((el) => el.textContent)
    expect(labels.sort()).toEqual(['유행 중', '평상'])
  })

  it('값이 전부 같으면(표준편차 0) 기준선을 그리지 않는다', () => {
    const points = [
      { date: '2026-08-01', ratio: 42 },
      { date: '2026-08-02', ratio: 42 },
      { date: '2026-08-03', ratio: 42 },
    ]
    const { container } = render(<TrendChart points={points} label="관심도" />)
    expect(container.querySelectorAll('.trend-chart__tier-line').length).toBe(0)
  })
})

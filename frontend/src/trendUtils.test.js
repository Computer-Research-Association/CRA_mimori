import { describe, it, expect } from 'vitest'
import { valueGradientColor } from './trendUtils.js'

describe('valueGradientColor', () => {
  it('t=0(최저값)은 시안이다', () => {
    expect(valueGradientColor(0)).toBe('rgb(0, 240, 255)')
  })

  it('t=1(최고값)은 레드다', () => {
    expect(valueGradientColor(1)).toBe('rgb(255, 104, 0)')
  })

  it('t=1/3은 그린 정점이다', () => {
    expect(valueGradientColor(1 / 3)).toBe('rgb(0, 255, 129)')
  })

  it('범위를 벗어난 t는 clamp된다', () => {
    expect(valueGradientColor(-5)).toBe(valueGradientColor(0))
    expect(valueGradientColor(5)).toBe(valueGradientColor(1))
  })

  it('구간 사이 값은 선형 보간된다', () => {
    // t=1/6은 시안(t=0)과 그린(t=1/3) 사이 중점.
    expect(valueGradientColor(1 / 6)).toBe('rgb(0, 248, 192)')
  })
})

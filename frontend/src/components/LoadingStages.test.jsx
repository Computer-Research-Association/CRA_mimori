import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import LoadingStages from './LoadingStages.jsx'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('LoadingStages', () => {
  it('첫 메시지를 즉시 보여준다', () => {
    render(<LoadingStages messages={['첫번째', '두번째']} intervalMs={1000} />)
    expect(screen.getByText('첫번째')).toBeInTheDocument()
  })

  it('일정 시간마다 다음 메시지로 순환한다', async () => {
    render(<LoadingStages messages={['첫번째', '두번째', '세번째']} intervalMs={1000} />)
    expect(screen.getByText('첫번째')).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(1000)
    expect(screen.getByText('두번째')).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(1000)
    expect(screen.getByText('세번째')).toBeInTheDocument()

    await vi.advanceTimersByTimeAsync(1000)
    expect(screen.getByText('첫번째')).toBeInTheDocument()
  })

  it('메시지가 1개뿐이면 순환하지 않는다', async () => {
    render(<LoadingStages messages={['유일한 메시지']} intervalMs={1000} />)
    await vi.advanceTimersByTimeAsync(5000)
    expect(screen.getByText('유일한 메시지')).toBeInTheDocument()
  })

  it('언마운트 후에는 상태 업데이트 경고 없이 조용히 정리된다', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { unmount } = render(<LoadingStages messages={['a', 'b']} intervalMs={1000} />)
    unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(consoleErrorSpy).not.toHaveBeenCalled()
  })
})

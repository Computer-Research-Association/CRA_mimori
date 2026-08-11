import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import CrawlRequestPanel from './CrawlRequestPanel.jsx'
import * as api from '../api.js'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('CrawlRequestPanel', () => {
  it('마운트되면 requestCrawl을 1회 호출한다', async () => {
    const requestCrawlSpy = vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({ keyword: '흘로망', status: 'queued' })

    render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)

    await waitFor(() => expect(requestCrawlSpy).toHaveBeenCalledWith('흘로망'))
    expect(requestCrawlSpy).toHaveBeenCalledTimes(1)
  })

  it('status가 done이 되면 onDone을 호출하고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    const fetchStatusSpy = vi.spyOn(api, 'fetchCrawlStatus')
      .mockResolvedValueOnce({ keyword: '흘로망', status: 'running' })
      .mockResolvedValueOnce({ keyword: '흘로망', status: 'done' })
    const onDone = vi.fn()

    render(<CrawlRequestPanel keyword="흘로망" onDone={onDone} />)

    await vi.advanceTimersByTimeAsync(5000)
    await vi.advanceTimersByTimeAsync(5000)

    expect(onDone).toHaveBeenCalledWith('흘로망')
    const callsAfterDone = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterDone)
  })

  it('status가 failed면 에러와 재시도 버튼을 보여준다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({
      keyword: '흘로망', status: 'failed', error: '크롤링 실패했습니다',
    })

    render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)
    await vi.advanceTimersByTimeAsync(5000)

    expect(await screen.findByText(/크롤링 실패했습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
  })

  it('진행 상황이 오면 단계와 소스별 결과를 보여준다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({
      keyword: '흘로망',
      status: 'running',
      stage: 'crawl',
      progress: {
        dcinside: { count: 12, status: 'ok' },
        youtube: { count: 0, status: 'ok' },
        namuwiki: { count: 0, status: '실패(HTTPError)' },
      },
    })

    render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)
    await vi.advanceTimersByTimeAsync(5000)

    expect(await screen.findByText(/웹에서 자료 수집 중/)).toBeInTheDocument()
    expect(screen.getByText('디시인사이드')).toBeInTheDocument()
    expect(screen.getByText('12건')).toBeInTheDocument()
    // 성공했지만 0건인 소스와 실패한 소스는 구분해서 보여야 한다
    expect(screen.getByText('자료 없음')).toBeInTheDocument()
    expect(screen.getByText('실패(HTTPError)')).toBeInTheDocument()
  })

  it('stage/progress가 없는 옛 응답에도 스피너만 띄우고 깨지지 않는다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({ keyword: '흘로망', status: 'running' })

    render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)
    await vi.advanceTimersByTimeAsync(5000)

    expect(await screen.findByText(/수집 중/)).toBeInTheDocument()
  })

  it('폴링 중 네트워크 오류가 발생하면 에러와 재시도 버튼을 보여주고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })
    const fetchStatusSpy = vi.spyOn(api, 'fetchCrawlStatus')
      .mockResolvedValueOnce({ keyword: '흘로망', status: 'running' })
      .mockRejectedValue(new Error('요청 실패 (500)'))

    render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)

    await vi.advanceTimersByTimeAsync(5000)
    await vi.advanceTimersByTimeAsync(5000)

    expect(await screen.findByText(/요청 실패 \(500\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()

    const callsAfterError = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterError)
  })

  it('초기 requestCrawl이 pending인 상태로 언마운트되면, 이후 resolve되어도 폴링을 시작하지 않는다', async () => {
    let resolveRequestCrawl
    const pendingRequestCrawl = new Promise((resolve) => {
      resolveRequestCrawl = resolve
    })
    vi.spyOn(api, 'requestCrawl').mockReturnValue(pendingRequestCrawl)
    const fetchStatusSpy = vi.spyOn(api, 'fetchCrawlStatus').mockResolvedValue({
      keyword: '흘로망', status: 'running',
    })
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { unmount } = render(<CrawlRequestPanel keyword="흘로망" onDone={vi.fn()} />)
    unmount()

    resolveRequestCrawl({ keyword: '흘로망', status: 'queued' })
    // flush the now-resolved requestCrawl().then(...) microtask
    await vi.advanceTimersByTimeAsync(0)
    // if startPolling() had run despite the unmount, an interval would now be
    // ticking; advance past one tick to be sure nothing was scheduled
    await vi.advanceTimersByTimeAsync(5000)

    expect(fetchStatusSpy).not.toHaveBeenCalled()
    expect(consoleErrorSpy).not.toHaveBeenCalled()
  })

  it('폴링 틱이 진행 중일 때 언마운트되면, 그 이후 done으로 resolve되어도 onDone을 호출하지 않는다', async () => {
    vi.spyOn(api, 'requestCrawl').mockResolvedValue({ keyword: '흘로망', status: 'queued' })

    let resolveInFlightPoll
    const inFlightPoll = new Promise((resolve) => {
      resolveInFlightPoll = resolve
    })
    const fetchStatusSpy = vi.spyOn(api, 'fetchCrawlStatus')
      .mockResolvedValueOnce({ keyword: '흘로망', status: 'running' })
      .mockReturnValueOnce(inFlightPoll)
    const onDone = vi.fn()

    const { unmount } = render(<CrawlRequestPanel keyword="흘로망" onDone={onDone} />)

    // requestCrawl 을 resolve시켜 폴링을 시작시킨다
    await vi.advanceTimersByTimeAsync(0)

    // 첫번째 틱: running (폴링 계속)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchStatusSpy).toHaveBeenCalledTimes(1)

    // 두번째 틱 시작: fetchCrawlStatus가 아직 pending인 상태
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchStatusSpy).toHaveBeenCalledTimes(2)

    // 두번째 틱이 in-flight인 상태에서 언마운트
    unmount()

    // 언마운트 이후에 in-flight였던 fetchCrawlStatus가 done으로 resolve됨
    resolveInFlightPoll({ keyword: '흘로망', status: 'done' })
    await vi.advanceTimersByTimeAsync(0)

    expect(onDone).not.toHaveBeenCalled()
  })
})

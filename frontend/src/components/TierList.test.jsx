import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import TierList from './TierList.jsx'
import * as api from '../api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('TierList', () => {
  it('순위/키워드/상태/z-score를 순서대로 보여준다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { date: '2026-11-03', daily_refresh: true, keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
      { date: '2026-11-03', daily_refresh: true, keyword: '야르', status: '평상', z_score: 0.44, final_z: 0.44 },
    ])
    render(<TierList onSelect={() => {}} />)

    expect(await screen.findByText('늙크크')).toBeInTheDocument()
    expect(screen.getByText('핫함')).toBeInTheDocument()
    expect(screen.getByText('+2.41')).toBeInTheDocument()
    expect(screen.getByText('야르')).toBeInTheDocument()
    expect(screen.getByText('+0.44')).toBeInTheDocument()
  })

  it('음수 z-score는 부호를 그대로 보여준다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { date: '2026-11-03', daily_refresh: true, keyword: '쌰갈', status: '소멸', z_score: -2.31, final_z: -2.31 },
    ])
    render(<TierList onSelect={() => {}} />)
    expect(await screen.findByText('-2.31')).toBeInTheDocument()
  })

  it('행을 클릭하면 onSelect가 해당 키워드와 함께 호출된다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { date: '2026-11-03', daily_refresh: true, keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
    ])
    const onSelect = vi.fn()
    render(<TierList onSelect={onSelect} />)
    await userEvent.click(await screen.findByText('늙크크'))
    expect(onSelect).toHaveBeenCalledWith('늙크크')
  })

  it('status가 null인 키워드는 "순위 집계 전" 그룹으로 분리되고, 순위표 쪽은 그대로 남는다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { date: '2026-11-03', daily_refresh: true, keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
      { date: '2026-11-03', daily_refresh: true, keyword: '막등록됨', status: null, z_score: null, final_z: null },
    ])
    render(<TierList onSelect={() => {}} />)
    expect(await screen.findByText('막 등록됨 · 순위 집계 전')).toBeInTheDocument()
    expect(screen.getByText('막등록됨')).toBeInTheDocument()
    // 순위표 쪽(핫함 행)은 집계 전 그룹과 별개로 그대로 보여야 한다.
    expect(screen.getByText('+2.41')).toBeInTheDocument()
  })

  it('상태가 "데이터 부족"인 키워드는 점수가 있어도 순위표에서 빠지고 "신호 부족" 그룹으로 분리된다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { date: '2026-11-03', daily_refresh: true, keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
      { date: '2026-11-03', daily_refresh: true, keyword: '신호부족어', status: '데이터 부족', z_score: 0, final_z: 0 },
    ])
    render(<TierList onSelect={() => {}} />)
    expect(await screen.findByText('신호 부족')).toBeInTheDocument()
    expect(screen.getByText('신호부족어')).toBeInTheDocument()
    // "막 등록됨" 문구는 배치가 아직 안 돈 null 상태 전용이라, 데이터 부족에는 안 붙어야 한다.
    expect(screen.queryByText('막 등록됨 · 순위 집계 전')).not.toBeInTheDocument()
    // 순위표 본문(핫함 칩)에는 "데이터 부족" 라벨이 없어야 한다.
    expect(screen.queryByText('데이터 부족')).not.toBeInTheDocument()
  })

  it('null(집계 전)과 "데이터 부족"(신호 없음)이 섞여 있으면 각자 다른 그룹·문구로 분리된다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { date: '2026-11-03', daily_refresh: true, keyword: '막등록됨', status: null, z_score: null, final_z: null },
      { date: '2026-11-03', daily_refresh: true, keyword: '신호부족어', status: '데이터 부족', z_score: 0, final_z: 0 },
    ])
    render(<TierList onSelect={() => {}} />)
    expect(await screen.findByText('막 등록됨 · 순위 집계 전')).toBeInTheDocument()
    expect(screen.getByText('신호 부족')).toBeInTheDocument()
    expect(screen.getByText('막등록됨')).toBeInTheDocument()
    expect(screen.getByText('신호부족어')).toBeInTheDocument()
  })

  it('빈 목록이면 아무것도 렌더링하지 않는다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([])
    const { container } = render(<TierList onSelect={() => {}} />)
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it('불러오기 실패 시 에러 메시지를 보여준다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockRejectedValue(new Error('네트워크 오류'))
    render(<TierList onSelect={() => {}} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('네트워크 오류')
  })
})

it('과거 집계일을 표시하고 즉시 분석 완료를 약속하지 않는다', async () => {
  vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
    { date: '2026-09-24', daily_refresh: true, keyword: '요이', status: '핫함', final_z: 3.27 },
  ])
  render(<TierList onSelect={() => {}} />)
  expect(await screen.findByText('2026-09-24 기준')).toBeInTheDocument()
  expect(screen.getByText('최근 집계 순위')).toBeInTheDocument()
  expect(screen.queryByText(/이미 분석까지 끝나/)).not.toBeInTheDocument()
})


it('오래된 고득점과 비정기 키워드는 최근 순위에 섞지 않는다', async () => {
  const onSelect = vi.fn()
  vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
    { keyword: '과거1위', date: '2026-09-26', daily_refresh: false, status: '핫함', final_z: 8 },
    { keyword: '갱신지연', date: '2026-11-02', daily_refresh: true, status: '핫함', final_z: 4 },
    { keyword: '오늘자료', date: '2026-11-03', daily_refresh: true, status: '평상', final_z: 0.2 },
    { keyword: '비정기오늘', date: '2026-11-03', daily_refresh: false, status: '핫함', final_z: 5 },
  ])
  const { container } = render(<TierList onSelect={onSelect} />)
  await screen.findByText('오늘자료')
  expect(container.querySelectorAll('.tier-row')).toHaveLength(1)
  expect(screen.queryByText('+8.00')).not.toBeInTheDocument()
  expect(screen.getByText('이전 집계 · 보관 자료')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '과거1위 · 2026-09-26' }))
  expect(onSelect).toHaveBeenCalledWith('과거1위')
})

it('새벽 2시 전에는 전날 자료를 갱신 누락으로 안내하지 않는다', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-11-04T15:30:00Z'))
  vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
    { keyword: '오늘자료', date: '2026-11-04', daily_refresh: true, status: '평상', final_z: 0.2 },
  ])
  try {
    render(<TierList onSelect={() => {}} />)
    await screen.findByText('오늘자료')
    expect(screen.queryByText(/오늘 집계가 아직/)).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

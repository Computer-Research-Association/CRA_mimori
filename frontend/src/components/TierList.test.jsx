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
      { keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
      { keyword: '야르', status: '평상', z_score: 0.44, final_z: 0.44 },
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
      { keyword: '쌰갈', status: '소멸', z_score: -2.31, final_z: -2.31 },
    ])
    render(<TierList onSelect={() => {}} />)
    expect(await screen.findByText('-2.31')).toBeInTheDocument()
  })

  it('행을 클릭하면 onSelect가 해당 키워드와 함께 호출된다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
    ])
    const onSelect = vi.fn()
    render(<TierList onSelect={onSelect} />)
    await userEvent.click(await screen.findByText('늙크크'))
    expect(onSelect).toHaveBeenCalledWith('늙크크')
  })

  it('status가 null인 키워드는 "순위 집계 전" 그룹으로 분리되고, 순위표 쪽은 그대로 남는다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
      { keyword: '막등록됨', status: null, z_score: null, final_z: null },
    ])
    render(<TierList onSelect={() => {}} />)
    expect(await screen.findByText('막 등록됨 · 순위 집계 전')).toBeInTheDocument()
    expect(screen.getByText('막등록됨')).toBeInTheDocument()
    // 순위표 쪽(핫함 행)은 집계 전 그룹과 별개로 그대로 보여야 한다.
    expect(screen.getByText('+2.41')).toBeInTheDocument()
  })

  it('상태가 "데이터 부족"인 키워드는 점수가 있어도 순위표에서 빠지고 "신호 부족" 그룹으로 분리된다', async () => {
    vi.spyOn(api, 'fetchTrendLeaderboard').mockResolvedValue([
      { keyword: '늙크크', status: '핫함', z_score: 2.41, final_z: 2.41 },
      { keyword: '신호부족어', status: '데이터 부족', z_score: 0, final_z: 0 },
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
      { keyword: '막등록됨', status: null, z_score: null, final_z: null },
      { keyword: '신호부족어', status: '데이터 부족', z_score: 0, final_z: 0 },
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

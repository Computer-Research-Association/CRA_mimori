import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import AdminPage from './AdminPage.jsx'
import * as api from './api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

const baseStats = {
  keyword_count: 22,
  keyword_cap: 60,
  collection_counts: { memes: 1791, cleaned_memes: 1200 },
  per_keyword_doc_counts: [
    { keyword: '야르', total: 108, by_source: { dcinside: 80, youtube: 28 } },
  ],
  capped_keywords: [],
  disk_usage: { total_gb: 100, used_gb: 30, free_gb: 70, used_percent: 30 },
}

function setup({ keywords = ['야르'], hidden = [], stats = baseStats } = {}) {
  vi.spyOn(api, 'fetchKeywords').mockResolvedValue(keywords)
  vi.spyOn(api, 'fetchHiddenKeywords').mockResolvedValue(hidden)
  vi.spyOn(api, 'fetchAdminStats').mockResolvedValue(stats)
}

describe('AdminPage', () => {
  it('별도 페이지로 렌더링되며 홈으로 돌아가는 링크가 있다', async () => {
    setup()
    render(<AdminPage />)
    const home = await screen.findByRole('link', { name: 'MEMEMORY' })
    expect(home).toHaveAttribute('href', '/')
  })

  it('서버 현황(키워드 개수/상한, 컬렉션별 문서 수)을 보여준다', async () => {
    setup()
    render(<AdminPage />)
    expect(await screen.findByText('22')).toBeInTheDocument()
    expect(screen.getByText(/60개/)).toBeInTheDocument()
    expect(screen.getByText('1,791건')).toBeInTheDocument()
  })

  it('디스크 사용량(전체/사용/잔여)을 보여준다', async () => {
    setup()
    render(<AdminPage />)
    expect(await screen.findByText('30GB')).toBeInTheDocument()
    expect(screen.getByText('100GB')).toBeInTheDocument()
    expect(screen.getByText('70GB')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()
  })

  it('디스크 여유 공간이 부족하면(85% 이상) 경고를 보여준다', async () => {
    setup({ stats: { ...baseStats, disk_usage: { total_gb: 100, used_gb: 90, free_gb: 10, used_percent: 90 } } })
    render(<AdminPage />)
    expect(await screen.findByText('여유 공간 부족')).toBeInTheDocument()
  })

  it('디스크 사용량을 못 읽으면(disk_usage: null) 안내 문구만 보여준다', async () => {
    setup({ stats: { ...baseStats, disk_usage: null } })
    render(<AdminPage />)
    expect(await screen.findByText('디스크 사용량을 읽을 수 없습니다.')).toBeInTheDocument()
  })

  it('상한에 걸린 키워드가 있으면 알림을 보여준다', async () => {
    setup({ stats: { ...baseStats, keyword_count: 60, capped_keywords: ['막힌키워드'] } })
    render(<AdminPage />)
    expect(await screen.findByText(/막힌키워드/)).toBeInTheDocument()
    expect(screen.getByText('상한 도달')).toBeInTheDocument()
  })

  it('키워드 행을 누르면 출처별 문서 수가 펼쳐진다', async () => {
    setup()
    const { container } = render(<AdminPage />)
    await screen.findByText('108건')
    const row = container.querySelector('.admin-source-table__row')
    expect(screen.queryByText('디시인사이드')).not.toBeInTheDocument()

    await userEvent.click(row)
    expect(screen.getByText('디시인사이드')).toBeInTheDocument()
    expect(screen.getByText('80건')).toBeInTheDocument()
    expect(screen.getByText('유튜브')).toBeInTheDocument()
    expect(screen.getByText('28건')).toBeInTheDocument()

    await userEvent.click(row)
    expect(screen.queryByText('디시인사이드')).not.toBeInTheDocument()
  })

  it('검색 목록과 숨긴 키워드를 함께 보여준다', async () => {
    setup({ keywords: ['야르'], hidden: ['오운완'] })
    render(<AdminPage />)
    expect(await screen.findByText('오운완')).toBeInTheDocument()
    expect(screen.getAllByText('야르').length).toBeGreaterThan(0)
  })

  it('숨기기를 누르면 hideKeyword를 호출하고 검색 목록에서 사라진다', async () => {
    setup({ keywords: ['야르'] })
    const hideSpy = vi.spyOn(api, 'hideKeyword').mockResolvedValue({ keyword: '야르', hidden: true })

    render(<AdminPage />)
    await userEvent.click(await screen.findByRole('button', { name: '숨기기' }))

    expect(hideSpy).toHaveBeenCalledWith('야르')
    expect(await screen.findByRole('button', { name: '복구' })).toBeInTheDocument()
  })

  it('완전삭제는 키워드를 정확히 입력해야 확정 버튼이 활성화된다', async () => {
    setup({ keywords: [], hidden: ['오운완'] })
    const deleteSpy = vi.spyOn(api, 'deleteKeywordPermanently').mockResolvedValue({ keyword: '오운완', deleted: true })

    render(<AdminPage />)
    await screen.findByText('오운완')
    await userEvent.click(screen.getByRole('button', { name: '완전삭제' }))

    const confirmButton = screen.getByRole('button', { name: '완전삭제 확정' })
    expect(confirmButton).toBeDisabled()

    const input = screen.getByLabelText('오운완 완전삭제 확인 입력')
    await userEvent.type(input, '오운완')
    expect(confirmButton).toBeEnabled()

    await userEvent.click(confirmButton)
    expect(deleteSpy).toHaveBeenCalledWith('오운완')
  })
})

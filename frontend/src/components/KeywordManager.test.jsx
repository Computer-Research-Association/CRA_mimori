import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import KeywordManager from './KeywordManager.jsx'
import * as api from '../api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('KeywordManager', () => {
  it('기본 상태에서는 관리 목록이 접혀있다', () => {
    render(<KeywordManager keywords={['야르']} onHidden={vi.fn()} onUnhidden={vi.fn()} onDeleted={vi.fn()} />)
    expect(screen.queryByText('검색 목록')).not.toBeInTheDocument()
  })

  it('키워드 관리를 열면 검색 목록과 숨긴 키워드를 보여준다', async () => {
    vi.spyOn(api, 'fetchHiddenKeywords').mockResolvedValue(['오운완'])
    render(<KeywordManager keywords={['야르']} onHidden={vi.fn()} onUnhidden={vi.fn()} onDeleted={vi.fn()} />)

    await userEvent.click(screen.getByRole('button', { name: '키워드 관리' }))

    expect(screen.getByText('야르')).toBeInTheDocument()
    expect(await screen.findByText('오운완')).toBeInTheDocument()
  })

  it('숨기기를 누르면 hideKeyword를 호출하고 onHidden을 알린다', async () => {
    vi.spyOn(api, 'fetchHiddenKeywords').mockResolvedValue([])
    const hideSpy = vi.spyOn(api, 'hideKeyword').mockResolvedValue({ keyword: '야르', hidden: true })
    const onHidden = vi.fn()

    render(<KeywordManager keywords={['야르']} onHidden={onHidden} onUnhidden={vi.fn()} onDeleted={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: '키워드 관리' }))
    await userEvent.click(screen.getByRole('button', { name: '숨기기' }))

    expect(hideSpy).toHaveBeenCalledWith('야르')
    expect(onHidden).toHaveBeenCalledWith('야르')
  })

  it('복구를 누르면 unhideKeyword를 호출하고 onUnhidden을 알린다', async () => {
    vi.spyOn(api, 'fetchHiddenKeywords').mockResolvedValue(['오운완'])
    const unhideSpy = vi.spyOn(api, 'unhideKeyword').mockResolvedValue({ keyword: '오운완', hidden: false })
    const onUnhidden = vi.fn()

    render(<KeywordManager keywords={[]} onHidden={vi.fn()} onUnhidden={onUnhidden} onDeleted={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: '키워드 관리' }))
    await screen.findByText('오운완')
    await userEvent.click(screen.getByRole('button', { name: '복구' }))

    expect(unhideSpy).toHaveBeenCalledWith('오운완')
    expect(onUnhidden).toHaveBeenCalledWith('오운완')
  })

  it('완전삭제는 키워드를 정확히 입력해야 확정 버튼이 활성화된다', async () => {
    vi.spyOn(api, 'fetchHiddenKeywords').mockResolvedValue(['오운완'])
    const deleteSpy = vi.spyOn(api, 'deleteKeywordPermanently').mockResolvedValue({ keyword: '오운완', deleted: true })
    const onDeleted = vi.fn()

    render(<KeywordManager keywords={[]} onHidden={vi.fn()} onUnhidden={vi.fn()} onDeleted={onDeleted} />)
    await userEvent.click(screen.getByRole('button', { name: '키워드 관리' }))
    await screen.findByText('오운완')
    await userEvent.click(screen.getByRole('button', { name: '완전삭제' }))

    const confirmButton = screen.getByRole('button', { name: '완전삭제 확정' })
    expect(confirmButton).toBeDisabled()

    const input = screen.getByLabelText('오운완 완전삭제 확인 입력')
    await userEvent.type(input, '오운완')
    expect(confirmButton).toBeEnabled()

    await userEvent.click(confirmButton)
    expect(deleteSpy).toHaveBeenCalledWith('오운완')
    expect(onDeleted).toHaveBeenCalledWith('오운완')
  })
})

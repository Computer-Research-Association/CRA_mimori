import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import RagPanel from './RagPanel.jsx'
import * as api from '../api.js'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RagPanel', () => {
  it('기본적으로 모든 소스가 체크된 상태로 시작한다', () => {
    render(<RagPanel keyword="야르" />)
    for (const source of api.AVAILABLE_SOURCES) {
      expect(screen.getByLabelText(source)).toBeChecked()
    }
  })

  it('질문을 보내면 체크된 소스만 askRag에 전달된다', async () => {
    const askRagSpy = vi.spyOn(api, 'askRag').mockResolvedValue({
      answer: '야르는 감탄사입니다', sources: [{ title: '제목', url: 'https://example.com' }], trend: null,
    })

    render(<RagPanel keyword="야르" />)

    await userEvent.click(screen.getByLabelText('tavily'))  // tavily 체크 해제
    await userEvent.type(screen.getByRole('textbox', { name: '질문' }), '무슨 뜻이야?')
    await userEvent.click(screen.getByRole('button', { name: '질문하기' }))

    expect(askRagSpy).toHaveBeenCalledWith(
      '야르', '무슨 뜻이야?',
      expect.not.arrayContaining(['tavily']),
    )
    expect(await screen.findByText('야르는 감탄사입니다')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '제목' })).toHaveAttribute('href', 'https://example.com')
  })

  it('실패하면 에러 메시지를 보여준다', async () => {
    vi.spyOn(api, 'askRag').mockRejectedValue(new Error('검색 결과가 없습니다'))

    render(<RagPanel keyword="야르" />)
    await userEvent.type(screen.getByRole('textbox', { name: '질문' }), '아무거나')
    await userEvent.click(screen.getByRole('button', { name: '질문하기' }))

    expect(await screen.findByText('검색 결과가 없습니다')).toBeInTheDocument()
  })
})

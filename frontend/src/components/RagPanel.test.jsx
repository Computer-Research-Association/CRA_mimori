import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import RagPanel from './RagPanel.jsx'
import * as api from '../api.js'
import { sourceMetaFor } from '../sourceMeta.js'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('RagPanel', () => {
  it('질문을 제출하면 submitRagRequest를 호출한다', async () => {
    const submitSpy = vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchRagStatus').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })

    render(<RagPanel keyword="야르" />)
    const input = screen.getByLabelText('질문')
    fireEvent.change(input, { target: { value: '무슨 뜻이야?' } })
    screen.getByRole('button', { name: '질문하기' }).click()

    await waitFor(() => expect(submitSpy).toHaveBeenCalledWith('야르', '무슨 뜻이야?', expect.any(Array)))
  })

  it('status가 done이 되면 답변과 출처를 렌더링하고 폴링을 멈춘다', async () => {
    vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    const fetchStatusSpy = vi.spyOn(api, 'fetchRagStatus')
      .mockResolvedValueOnce({ job_id: '잡아이디', status: 'running' })
      .mockResolvedValueOnce({
        job_id: '잡아이디', status: 'done', answer: '답변입니다',
        sources: [{ title: '출처제목', url: 'https://example.com' }], trend: null, error: null,
      })

    render(<RagPanel keyword="야르" />)
    const input = screen.getByLabelText('질문')
    fireEvent.change(input, { target: { value: '무슨 뜻이야?' } })
    screen.getByRole('button', { name: '질문하기' }).click()

    await vi.advanceTimersByTimeAsync(3000)
    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText('출처제목')).toBeInTheDocument()
    const callsAfterDone = fetchStatusSpy.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetchStatusSpy.mock.calls.length).toBe(callsAfterDone)
  })

  it('status가 failed면 에러를 보여준다', async () => {
    vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchRagStatus').mockResolvedValue({
      job_id: '잡아이디', status: 'failed', error: 'RAG 처리 실패했습니다', answer: null, sources: null, trend: null,
    })

    render(<RagPanel keyword="야르" />)
    const input = screen.getByLabelText('질문')
    fireEvent.change(input, { target: { value: '무슨 뜻이야?' } })
    screen.getByRole('button', { name: '질문하기' }).click()

    await vi.advanceTimersByTimeAsync(3000)

    expect(await screen.findByText(/RAG 처리 실패했습니다/)).toBeInTheDocument()
  })

  it('예시 질문 칩을 클릭하면 해당 질문으로 바로 제출된다', async () => {
    const submitSpy = vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchRagStatus').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })

    render(<RagPanel keyword="야르" />)
    screen.getByRole('button', { name: '왜 유행했나요?' }).click()

    await waitFor(() => expect(submitSpy).toHaveBeenCalledWith('야르', '왜 유행했나요?', expect.any(Array)))
    expect(screen.getByLabelText('질문')).toHaveValue('왜 유행했나요?')
  })

  it('기본적으로 모든 소스가 체크된 상태로 시작한다', () => {
    render(<RagPanel keyword="야르" />)
    for (const source of api.AVAILABLE_SOURCES) {
      expect(screen.getByLabelText(sourceMetaFor(source).label)).toBeChecked()
    }
  })

  it('질문을 보내면 체크된 소스만 submitRagRequest에 전달된다', async () => {
    const submitSpy = vi.spyOn(api, 'submitRagRequest').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })
    vi.spyOn(api, 'fetchRagStatus').mockResolvedValue({ job_id: '잡아이디', status: 'queued' })

    render(<RagPanel keyword="야르" />)

    await userEvent.click(screen.getByLabelText(sourceMetaFor('tavily').label))  // tavily 체크 해제
    fireEvent.change(screen.getByLabelText('질문'), { target: { value: '무슨 뜻이야?' } })
    await userEvent.click(screen.getByRole('button', { name: '질문하기' }))

    expect(submitSpy).toHaveBeenCalledWith(
      '야르', '무슨 뜻이야?',
      expect.not.arrayContaining(['tavily']),
    )
  })

  it('모든 소스를 해제하면 질문하기 버튼이 비활성화된다', async () => {
    render(<RagPanel keyword="야르" />)

    for (const source of api.AVAILABLE_SOURCES) {
      await userEvent.click(screen.getByLabelText(sourceMetaFor(source).label))
    }

    expect(screen.getByRole('button', { name: '질문하기' })).toBeDisabled()
    expect(screen.getByText('최소 하나의 출처를 선택하세요')).toBeInTheDocument()
  })
})

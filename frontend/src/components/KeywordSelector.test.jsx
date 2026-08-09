import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import KeywordSelector from './KeywordSelector.jsx'

describe('KeywordSelector', () => {
  it('목록에 있는 키워드를 입력하면 onSelect가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['야르', '쌰갈']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('textbox'), '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(onSelect).toHaveBeenCalledWith('야르')
    expect(onNewKeyword).not.toHaveBeenCalled()
  })

  it('목록에 없는 키워드를 입력하면 onNewKeyword가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['야르']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('textbox'), '흘로망')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(onNewKeyword).toHaveBeenCalledWith('흘로망')
    expect(onSelect).not.toHaveBeenCalled()
  })
})

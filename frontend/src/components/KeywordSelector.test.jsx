import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import KeywordSelector from './KeywordSelector.jsx'

describe('KeywordSelector', () => {
  it('목록에 있는 키워드를 입력하면 onSelect가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['야르', '쌰갈']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('combobox'), '야르')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(onSelect).toHaveBeenCalledWith('야르')
    expect(onNewKeyword).not.toHaveBeenCalled()
  })

  it('목록에 없는 키워드를 입력하면 onNewKeyword가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['야르']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('combobox'), '흘로망')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(onNewKeyword).toHaveBeenCalledWith('흘로망')
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('표기만 다른 기존 키워드가 있으면 확인창을 띄우고 바로 등록하지 않는다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['거제 야호~']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('combobox'), '거제야호')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(onSelect).not.toHaveBeenCalled()
    expect(onNewKeyword).not.toHaveBeenCalled()
    expect(screen.getByText(/찾으시나요/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "'거제 야호~'" })).toBeInTheDocument()
  })

  it('확인창에서 후보를 누르면 onSelect가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['거제 야호~']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('combobox'), '거제야호')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))
    await userEvent.click(screen.getByRole('button', { name: "'거제 야호~'" }))

    expect(onSelect).toHaveBeenCalledWith('거제 야호~')
    expect(onNewKeyword).not.toHaveBeenCalled()
  })

  it('확인창에서 "아니요, 새로 등록"을 누르면 원래 입력 그대로 onNewKeyword가 호출된다', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(<KeywordSelector keywords={['거제 야호~']} onSelect={onSelect} onNewKeyword={onNewKeyword} />)

    await userEvent.type(screen.getByRole('combobox'), '거제야호')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))
    await userEvent.click(screen.getByRole('button', { name: /아니요.*새로 등록/ }))

    expect(onNewKeyword).toHaveBeenCalledWith('거제야호')
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('부분 포함 관계인 짧은 기존 키워드도 후보로 뜬다(예: "야호~")', async () => {
    const onSelect = vi.fn()
    const onNewKeyword = vi.fn()
    render(
      <KeywordSelector
        keywords={['거제 야호~', '야호~']}
        onSelect={onSelect}
        onNewKeyword={onNewKeyword}
      />
    )

    await userEvent.type(screen.getByRole('combobox'), '거제야호')
    await userEvent.click(screen.getByRole('button', { name: '검색' }))

    expect(screen.getByRole('button', { name: "'거제 야호~'" })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: "'야호~'" })).toBeInTheDocument()
  })

  it('입력창에 datalist로 기존 키워드 목록을 노출한다', () => {
    render(<KeywordSelector keywords={['야르', '쌰갈']} onSelect={vi.fn()} onNewKeyword={vi.fn()} />)

    const input = screen.getByRole('combobox')
    expect(input).toHaveAttribute('list', 'keyword-options-hero')
    const options = document.querySelectorAll('#keyword-options-hero option')
    expect(Array.from(options).map((o) => o.value)).toEqual(['야르', '쌰갈'])
  })
})

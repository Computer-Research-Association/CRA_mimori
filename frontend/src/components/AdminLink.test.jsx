import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import AdminLink from './AdminLink.jsx'

describe('AdminLink', () => {
  it('/admin으로 가는 링크이고, 관리자용임을 이름으로 확실히 드러낸다', () => {
    render(<AdminLink />)
    const link = screen.getByRole('link', { name: '관리자 화면' })
    expect(link).toHaveAttribute('href', '/admin')
  })

  it('새 탭으로 연다', () => {
    render(<AdminLink />)
    const link = screen.getByRole('link', { name: '관리자 화면' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })
})

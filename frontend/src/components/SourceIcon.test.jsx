import { render, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import SourceIcon from './SourceIcon.jsx'

describe('SourceIcon', () => {
  it('domain이 있으면 파비콘 이미지를 렌더링한다', () => {
    render(<SourceIcon meta={{ label: '유튜브', domain: 'youtube.com', emoji: '📺' }} />)
    expect(document.querySelector('img.source-icon')).toHaveAttribute(
      'src',
      'https://www.google.com/s2/favicons?domain=youtube.com&sz=32',
    )
  })

  it('domain이 없으면 처음부터 이모지를 렌더링한다', () => {
    render(<SourceIcon meta={{ label: '웹 검색', domain: null, emoji: '🌐' }} />)
    const icon = document.querySelector('.source-icon--emoji')
    expect(icon).toHaveTextContent('🌐')
    expect(icon).toHaveAttribute('aria-hidden', 'true')
  })

  it('이미지 로딩이 실패하면 이모지로 대체된다', () => {
    render(<SourceIcon meta={{ label: '나무위키', domain: 'namu.wiki', emoji: '📖' }} />)
    const img = document.querySelector('img.source-icon')
    fireEvent.error(img)
    expect(document.querySelector('.source-icon--emoji')).toHaveTextContent('📖')
  })

  it('아이콘은 항상 장식용이라 접근 가능한 이름을 따로 갖지 않는다', () => {
    render(<SourceIcon meta={{ label: '유튜브', domain: 'youtube.com', emoji: '📺' }} />)
    expect(document.querySelector('img.source-icon')).toHaveAttribute('alt', '')
  })
})

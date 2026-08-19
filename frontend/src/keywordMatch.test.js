import { describe, it, expect } from 'vitest'
import { normalizeKeyword, findSimilarKeywords } from './keywordMatch.js'

describe('normalizeKeyword', () => {
  it('공백과 물결표를 제거하고 소문자로 맞춘다', () => {
    expect(normalizeKeyword('거제 야호~')).toBe('거제야호')
    expect(normalizeKeyword('거제야호')).toBe('거제야호')
    expect(normalizeKeyword('Hello')).toBe('hello')
  })

  it('빈 문자열/undefined는 빈 문자열을 반환한다', () => {
    expect(normalizeKeyword('')).toBe('')
    expect(normalizeKeyword(undefined)).toBe('')
  })
})

describe('findSimilarKeywords', () => {
  const existing = ['거제 야호~', '야호~', '아이폰']

  it('공백/물결표 차이만 있는 표기를 찾는다(포함 관계인 "야호~"도 같이 걸림)', () => {
    // "거제야호"는 "거제 야호~"와는 표기 차이(정규화 후 동일), "야호~"와는 포함
    // 관계(정규화한 "야호"가 "거제야호"의 부분 문자열)라 둘 다 걸리는 게 맞다.
    expect(findSimilarKeywords('거제야호', existing).sort()).toEqual(['거제 야호~', '야호~'].sort())
  })

  it('포함 관계인 짧은 키워드도 찾는다(둘 다)', () => {
    // "야호~"를 입력하면 그걸 포함하는 "거제 야호~"도, 자기 자신과 정규화가
    // 같은(그리고 원문이 다른) "야호~" 자기 자신은 원문이 같아 제외된다.
    expect(findSimilarKeywords('야호', existing).sort()).toEqual(['거제 야호~', '야호~'].sort())
  })

  it('완전히 동일한 원문은 결과에서 빠진다(호출부가 exact-match로 처리)', () => {
    expect(findSimilarKeywords('야호~', existing)).not.toContain('야호~')
  })

  it('무관한 키워드는 안 걸린다', () => {
    expect(findSimilarKeywords('아이패드', existing)).toEqual([])
  })

  it('입력이 너무 짧으면(1글자) 빈 배열을 반환한다', () => {
    expect(findSimilarKeywords('야', existing)).toEqual([])
  })

  it('기존 키워드가 정규화 후 1글자면 후보에서 제외된다', () => {
    expect(findSimilarKeywords('아야', ['아'])).toEqual([])
  })
})

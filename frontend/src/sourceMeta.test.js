import { describe, it, expect } from 'vitest'
import { SOURCE_META, faviconUrl, sourceMetaFor, sourceMetaForUrl } from './sourceMeta.js'

describe('faviconUrl', () => {
  it('domain이 있으면 구글 파비콘 URL을 만든다', () => {
    expect(faviconUrl('youtube.com')).toBe('https://www.google.com/s2/favicons?domain=youtube.com&sz=32')
  })

  it('domain이 없으면 null을 반환한다', () => {
    expect(faviconUrl(null)).toBeNull()
  })
})

describe('sourceMetaFor', () => {
  it('알려진 source key면 해당 메타를 반환한다', () => {
    expect(sourceMetaFor('youtube')).toBe(SOURCE_META.youtube)
  })

  it('모르는 source key면 기본값을 반환한다', () => {
    expect(sourceMetaFor('없는소스').label).toBe('기타 출처')
  })
})

describe('sourceMetaForUrl', () => {
  it('알려진 도메인이 포함된 URL이면 해당 메타를 반환한다', () => {
    expect(sourceMetaForUrl('https://www.youtube.com/watch?v=abc')).toBe(SOURCE_META.youtube)
    expect(sourceMetaForUrl('https://gall.dcinside.com/board/view/?id=123')).toBe(SOURCE_META.dcinside)
  })

  it('알려지지 않은 도메인이면 기본값을 반환한다', () => {
    expect(sourceMetaForUrl('https://example.com/post/1').label).toBe('기타 출처')
  })

  it('url이 없거나 잘못된 형식이면 기본값을 반환한다', () => {
    expect(sourceMetaForUrl(null).label).toBe('기타 출처')
    expect(sourceMetaForUrl('링크 없음').label).toBe('기타 출처')
  })
})

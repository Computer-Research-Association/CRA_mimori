import { useState } from 'react'
import { faviconUrl } from '../sourceMeta.js'

export default function SourceIcon({ meta, size = 16 }) {
  const [imgFailed, setImgFailed] = useState(false)
  const url = faviconUrl(meta.domain)

  // 항상 장식용이다 — 접근 가능한 이름은 옆에 붙는 실제 텍스트(체크박스 라벨,
  // 출처 링크 제목)가 담당하므로, 여기서 aria-label을 따로 붙이면 같은 라벨을
  // 가진 요소가 두 개가 되어 스크린리더/getByLabelText 조회 양쪽에서 중복이 생긴다.
  if (!url || imgFailed) {
    return (
      <span className="source-icon source-icon--emoji" aria-hidden="true">
        {meta.emoji}
      </span>
    )
  }

  return (
    <img
      className="source-icon"
      src={url}
      alt=""
      width={size}
      height={size}
      onError={() => setImgFailed(true)}
    />
  )
}

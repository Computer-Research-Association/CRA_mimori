import { AVAILABLE_SOURCES } from '../api.js'
import { sourceMetaFor } from '../sourceMeta.js'
import SourceIcon from './SourceIcon.jsx'

export default function SourceFilter({ selected, onChange }) {
  function toggle(source) {
    if (selected.includes(source)) {
      onChange(selected.filter((s) => s !== source))
    } else {
      onChange([...selected, source])
    }
  }

  return (
    <fieldset className="chip-group">
      <legend>출처 필터 — 체크된 곳의 문서만 분석에 쓰인다</legend>
      {AVAILABLE_SOURCES.map((source) => {
        const meta = sourceMetaFor(source)
        return (
          <label key={source} className="chip">
            <input
              type="checkbox"
              checked={selected.includes(source)}
              onChange={() => toggle(source)}
              aria-label={meta.label}
            />
            <SourceIcon meta={meta} />
            {meta.label}
          </label>
        )
      })}
    </fieldset>
  )
}

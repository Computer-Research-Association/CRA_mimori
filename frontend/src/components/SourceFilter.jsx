import { AVAILABLE_SOURCES } from '../api.js'

export default function SourceFilter({ selected, onChange }) {
  function toggle(source) {
    if (selected.includes(source)) {
      onChange(selected.filter((s) => s !== source))
    } else {
      onChange([...selected, source])
    }
  }

  return (
    <fieldset>
      <legend>출처 필터</legend>
      {AVAILABLE_SOURCES.map((source) => (
        <label key={source}>
          <input
            type="checkbox"
            checked={selected.includes(source)}
            onChange={() => toggle(source)}
          />
          {source}
        </label>
      ))}
    </fieldset>
  )
}

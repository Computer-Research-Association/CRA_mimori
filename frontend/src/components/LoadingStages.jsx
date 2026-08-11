import { useEffect, useState } from 'react'

export default function LoadingStages({ messages, intervalMs = 3000 }) {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    setIndex(0)
    if (messages.length <= 1) return
    const timer = setInterval(() => {
      setIndex((i) => (i + 1) % messages.length)
    }, intervalMs)
    return () => clearInterval(timer)
  }, [messages, intervalMs])

  return (
    <p className="loading-line">
      <span className="spinner" aria-hidden="true" />
      {messages[index]}
    </p>
  )
}

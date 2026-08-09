import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import App from './App.jsx'

describe('App', () => {
  it('렌더링된다', () => {
    render(<App />)
    expect(screen.getByText(/mimori/)).toBeInTheDocument()
  })
})

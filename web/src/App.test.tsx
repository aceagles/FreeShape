import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from './App'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('shows the version from /health', async () => {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ ok: true, version: '9.9.9' })))
  render(<App />)
  expect(await screen.findByText('Server version 9.9.9')).toBeTruthy()
})

it('says so when the server does not answer', async () => {
  vi.stubGlobal('fetch', async () => new Response('', { status: 404 }))
  render(<App />)
  expect(await screen.findByText('The server did not answer.')).toBeTruthy()
})

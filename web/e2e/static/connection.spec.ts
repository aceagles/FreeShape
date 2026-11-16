import { expect, test } from '@playwright/test'

// The shell with the live connection state and the debug panel, against a scripted socket (no freeshape serve).
test('the connection panel shows the state and round trips, and a dropped socket reconnects', async ({ page }) => {
  const sockets: string[] = []
  // The test decides when the first socket drops and when the second one gets its hello, so the
  // Reconnecting state lasts exactly as long as the test needs it, whatever the machine's speed.
  let first: { close(): void } | undefined
  let allowSecondHello!: () => void
  const secondHello = new Promise<void>((resolve) => (allowSecondHello = resolve))
  // a server over one Document that is open: the client goes straight into it
  await page.route('**/health', (route) => route.fulfill({ json: { ok: true, version: '1.2.3' } }))
  await page.route(/\/documents(\/|$)/, async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/documents') return route.fulfill({ json: { documents: [{ id: 'd', name: 'Bracket', modified: 1, holder: null, open: true, missing: false }] } })
    return route.fulfill({ json: { document: { id: 'd', name: 'Bracket' }, tabs: [], read_only: false, holder: null } })
  })
  await page.routeWebSocket(/\/ws\?client=/, (ws) => {
    sockets.push(ws.url())
    const hello = JSON.stringify({ type: 'hello', client: 'x', document: { id: 'd', name: 'Bracket' }, tabs: [] })
    if (sockets.length === 1) {
      first = ws
      ws.send(hello)
    } else {
      void secondHello.then(() => ws.send(hello))
    }
    ws.onMessage((m) => {
      const msg = JSON.parse(String(m)) as { type: string; t: number }
      if (msg.type === 'ping') ws.send(JSON.stringify({ type: 'pong', t: msg.t }))
    })
  })
  await page.goto('/')
  // The debug panel opens from the connection state in the top bar.
  await expect(page.getByTestId('link-status')).toHaveText('Connected')
  await page.getByTestId('link-status').click()
  await expect(page.getByTestId('version')).toHaveText('Server version 1.2.3')
  await expect(page.getByTestId('connection')).toHaveText('Connected to Bracket')
  await expect(page.getByTestId('rtt-median')).toHaveText(/ms$/)
  await page.screenshot({ path: test.info().outputPath('connection-1024x768.png') })
  first?.close()
  await expect(page.getByTestId('link-status')).toHaveText('Reconnecting')
  await expect(page.getByTestId('connection')).toContainText('Not connected')
  allowSecondHello()
  await expect(page.getByTestId('connection')).toHaveText('Connected to Bracket', { timeout: 10_000 })
  expect(sockets.length).toBeGreaterThanOrEqual(2)
  expect(new Set(sockets.map((u) => new URL(u).searchParams.get('client'))).size).toBe(1)
})

import { expect, test } from '@playwright/test'

// The shell with the live connection state and the debug panel, against a scripted socket (no freeshape serve).
test('the connection panel shows the state and round trips, and a dropped socket reconnects', async ({ page }) => {
  const sockets: string[] = []
  await page.route('**/health', (route) => route.fulfill({ json: { ok: true, version: '1.2.3' } }))
  await page.routeWebSocket(/\/ws\?client=/, (ws) => {
    sockets.push(ws.url())
    ws.send(JSON.stringify({ type: 'hello', client: 'x', document: { id: 'd', name: 'Bracket' }, tabs: [] }))
    ws.onMessage((m) => {
      const msg = JSON.parse(String(m)) as { type: string; t: number }
      if (msg.type === 'ping') ws.send(JSON.stringify({ type: 'pong', t: msg.t }))
    })
    if (sockets.length === 1) setTimeout(() => ws.close(), 1500)
  })
  await page.goto('/')
  // The debug panel opens from the connection state in the top bar.
  await expect(page.getByTestId('link-status')).toHaveText('Connected')
  await page.getByTestId('link-status').click()
  await expect(page.getByTestId('version')).toHaveText('Server version 1.2.3')
  await expect(page.getByTestId('connection')).toHaveText('Connected to Bracket')
  await expect(page.getByTestId('rtt-median')).toHaveText(/ms$/)
  await page.screenshot({ path: test.info().outputPath('connection-1024x768.png') })
  await expect(page.getByTestId('link-status')).toHaveText('Reconnecting')
  await expect(page.getByTestId('connection')).toContainText('Not connected')
  await expect(page.getByTestId('connection')).toHaveText('Connected to Bracket', { timeout: 10_000 })
  expect(sockets.length).toBeGreaterThanOrEqual(2)
  expect(new Set(sockets.map((u) => new URL(u).searchParams.get('client'))).size).toBe(1)
})

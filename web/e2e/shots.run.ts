// Run by scripts/web/shots.sh (never by hand: it needs FS_BASE_URL, the server the script started).
// For each state in shots.ts and each size, under WebKit: capture the client, capture the same state of the mock
// (web/design/layout.html#<state>-<size>), and write the two side by side, mock on the left, to web/shots/out/.
// It is a way to look, not a comparison: a difference is never an error. Exit 1 only when a state could not be captured.
import { mkdirSync, writeFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'
import { webkit } from '@playwright/test'
import type { Browser } from '@playwright/test'
import { SIZES, shots } from './shots.ts'

const base = process.env.FS_BASE_URL
if (!base) throw new Error('FS_BASE_URL is not set: run this through scripts/web/shots.sh')
const out = resolve(import.meta.dirname, '../shots/out')
const layoutUrl = pathToFileURL(resolve(import.meta.dirname, '../design/layout.html')).href
const only = process.argv.slice(2)
const GAP = 24
const LABEL = 28

const failures: string[] = []
const written: string[] = []

async function capture(browser: Browser, width: number, height: number, key: string, name: string, mock: string | null, steps: (typeof shots)[number]['steps'], after?: (typeof shots)[number]['after']) {
  const stem = `${name}-${width}x${height}`
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: true, deviceScaleFactor: 1, baseURL: base })
  try {
    const page = await context.newPage()
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    try {
      await steps(page)
      // let fonts and the last frame settle
      await page.evaluate(() => document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))))
      await page.waitForTimeout(300)
    } catch (e) {
      const file = resolve(out, `${stem}.FAILED.png`)
      await page.screenshot({ path: file }).catch(() => {})
      await after?.(page).catch((e: Error) => console.log(`  restoring after ${stem}: ${e.message.split('\n')[0]}`))
      failures.push(`${stem}: steps failed: ${(e as Error).message.split('\n')[0]} (what was on screen: ${file})`)
      return
    }
    const client = await page.screenshot()
    await after?.(page).catch((e: Error) => console.log(`  restoring after ${stem}: ${e.message.split('\n')[0]}`))
    writeFileSync(resolve(out, `${stem}.client.png`), client)
    written.push(`${stem}.client.png`)
    for (const m of errors) console.log(`  page error in ${stem}: ${m}`)
    if (mock === null) return
    const mockPage = await context.newPage()
    await mockPage.goto(`${layoutUrl}#${mock}-${key}`)
    const mockShot = await mockPage.screenshot()
    await mockPage.close()
    // Both halves at the same scale, one label over each.
    const sheet = await context.newPage()
    await sheet.setViewportSize({ width: width * 2 + GAP, height: height + LABEL })
    const img = (b: Buffer) => `data:image/png;base64,${b.toString('base64')}`
    await sheet.setContent(
      `<body style="margin:0;background:#888;font:600 14px/${LABEL}px system-ui,sans-serif;color:#fff">` +
        `<div style="display:flex;gap:${GAP}px">` +
        `<div><div style="height:${LABEL}px;padding-left:8px">layout: ${mock} ${width}x${height}</div><img src="${img(mockShot)}" style="display:block"></div>` +
        `<div><div style="height:${LABEL}px;padding-left:8px">client: ${name} ${width}x${height}</div><img src="${img(client)}" style="display:block"></div>` +
        `</div></body>`,
    )
    await sheet.evaluate(() => Promise.all([...document.images].map((i) => i.decode())))
    writeFileSync(resolve(out, `${stem}.png`), await sheet.screenshot())
    written.push(`${stem}.png`)
  } catch (e) {
    failures.push(`${stem}: ${(e as Error).message.split('\n')[0]}`)
  } finally {
    await context.close().catch(() => {})
  }
}

mkdirSync(out, { recursive: true })
const browser = await webkit.launch()
try {
  for (const size of SIZES) {
    for (const s of shots) {
      if (only.length && !only.includes(s.name)) continue
      await capture(browser, size.width, size.height, size.key, s.name, s.mock === undefined ? s.name : s.mock, s.steps, s.after)
    }
  }
} finally {
  await browser.close()
}
for (const w of written) console.log(`wrote web/shots/out/${w}`)
for (const f of failures) console.error(`NOT CAPTURED ${f}`)
process.exit(failures.length ? 1 : 0)

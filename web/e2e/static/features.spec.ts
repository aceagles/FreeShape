import { expect, test } from '@playwright/test'
import { singleDocument } from '../support/scripted.ts'

// The feature list's CSS in the built client (no server): a long failed message and a long list stay inside the panel.
test('a long failed message wraps inside the row, and a long list scrolls inside the panel', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await singleDocument().install(page)
  await page.goto('/')
  await expect(page.getByTestId('document-name')).toHaveText('Bracket')
  const r = await page.evaluate(() => {
    const body = document.querySelector('.panel-body') as HTMLElement
    const column = document.querySelector('[data-region="left-column"]') as HTMLElement
    const long = 'x'.repeat(400)
    const rows = Array.from({ length: 60 }, (_, i) => `<li><button class="panel-row"><span class="row-text"><span class="row-name">Feature ${i}</span></span></button></li>`).join('')
    body.innerHTML = `<ul class="feature-list"><li><button class="panel-row" data-state="failed"><span class="row-text"><span class="row-name">Hole 1 failed</span><span class="row-message">${long}</span></span></button></li>${rows}</ul>`
    const failed = body.querySelector('[data-state="failed"]') as HTMLElement
    return {
      column: column.getBoundingClientRect().width,
      row: failed.getBoundingClientRect().width,
      rowHeight: failed.getBoundingClientRect().height,
      scrolls: body.scrollHeight > body.clientHeight,
      overflowY: getComputedStyle(body).overflowY,
      page: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
    }
  })
  expect(r.row).toBeLessThanOrEqual(r.column)
  expect(r.rowHeight).toBeLessThan(120) // three lines of message at most
  expect(r.scrolls).toBe(true)
  expect(r.overflowY).toBe('auto')
  expect(r.page).toEqual([1024, 768])
})

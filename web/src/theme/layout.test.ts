// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { buildLayoutPage, modes } from './layoutPage.ts'
import { screens, size } from './tokens.ts'

describe('web/design/layout.html', () => {
  const html = buildLayoutPage()

  it('is up to date with src/theme/tokens.ts (run `npm run design:build`)', () => {
    const file = readFileSync(new URL('../../design/layout.html', import.meta.url), 'utf8')
    expect(file).toBe(html)
  })

  it('has every state at both sizes', () => {
    for (const key of Object.keys(screens)) for (const mode of modes) expect(html).toContain(`id="${mode}-${key}"`)
  })

  it('loads nothing from the network', () => {
    expect(html).not.toMatch(/(src|href)="https?:/)
    expect(html).not.toMatch(/@import|url\(/)
  })
})

describe('tokens', () => {
  it('holds the sizes the card names', () => {
    expect(size.topBar).toBe(52)
    expect(size.toolStrip).toBe(64)
    expect(size.toolButton).toEqual({ width: 72, height: 56 })
    expect(screens['1024'].panelWidth).toBe(280)
    expect(screens['1366'].panelWidth).toBe(320)
    expect(size.rail).toBe(56)
    expect(size.padKeyHeight).toBe(46)
    expect(size.padColumns).toBe(5)
    expect(size.viewCube).toBe(72)
    expect(size.groupRow).toBe(52)
    expect(size.pinButton).toBe(44)
    expect(size.quickMenuButton).toEqual({ width: 64, height: 48 })
    expect(size.quickMenuRadius).toBe(96)
  })
})

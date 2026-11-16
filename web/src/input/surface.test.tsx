import { readFileSync } from 'node:fs'
import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InlineNumber } from '../dialog/InlineNumber'
import { inTextEntry, installSurfaceGuards } from './surface'

let off: () => void
beforeEach(() => {
  off = installSurfaceGuards()
})
afterEach(() => {
  off()
  document.body.replaceChildren()
})

const add = (html: string): HTMLElement => {
  const host = document.createElement('div')
  host.innerHTML = html
  document.body.append(host)
  return host
}
const fireOn = (el: Element, type: string): Event => {
  const e = new Event(type, { bubbles: true, cancelable: true })
  el.dispatchEvent(e)
  return e
}

describe('the surface guards', () => {
  it('prevent contextmenu and selectstart on a sketch label, a chip, a canvas and a portalled dialog', () => {
    const host = add(
      '<div class="viewport"><canvas></canvas><span class="dim-label">12.5</span></div><div class="chip-slot"><button class="chip">1 face</button></div><div class="dialog-floating"><label>Depth</label></div>',
    )
    for (const sel of ['canvas', '.dim-label', '.chip', 'label']) {
      const el = host.querySelector(sel)!
      expect(fireOn(el, 'contextmenu').defaultPrevented, `contextmenu ${sel}`).toBe(true)
      expect(fireOn(el, 'selectstart').defaultPrevented, `selectstart ${sel}`).toBe(true)
    }
  })

  it('leave contextmenu and selectstart alone in an input, a textarea, a contenteditable and inside it', () => {
    const host = add('<input type="text" /><textarea></textarea><div contenteditable="true"><b>x</b></div><span class="inline-number"><input class="inline-number-field" /></span>')
    for (const sel of ['input', 'textarea', '[contenteditable] b', '.inline-number-field']) {
      const el = host.querySelector(sel)!
      expect(inTextEntry(el)).toBe(true)
      expect(fireOn(el, 'contextmenu').defaultPrevented, `contextmenu ${sel}`).toBe(false)
      expect(fireOn(el, 'selectstart').defaultPrevented, `selectstart ${sel}`).toBe(false)
    }
  })

  it('leave the real typed number field (InlineNumber) alone, and still guard the label beside it', () => {
    const { container } = render(
      <div>
        <span data-testid="label">Depth</span>
        <InlineNumber value="10 mm" label="Depth" onCommit={vi.fn()} />
      </div>,
    )
    const field = container.querySelector('input')!
    expect(inTextEntry(field)).toBe(true)
    expect(fireOn(field, 'contextmenu').defaultPrevented).toBe(false)
    expect(fireOn(field, 'selectstart').defaultPrevented).toBe(false)
    expect(fireOn(container.querySelector('[data-testid="label"]')!, 'selectstart').defaultPrevented).toBe(true)
  })

  it('treat a text node inside a label as the label', () => {
    const host = add('<span>text</span>')
    const e = new Event('selectstart', { bubbles: true, cancelable: true })
    host.querySelector('span')!.firstChild!.dispatchEvent(e)
    expect(e.defaultPrevented).toBe(true)
  })

  it('stop when removed', () => {
    off()
    const host = add('<span>x</span>')
    expect(fireOn(host.firstElementChild!, 'contextmenu').defaultPrevented).toBe(false)
    off = installSurfaceGuards()
  })
})

/** The CSS half: the block in shell.css. A real browser's computed styles are checked by web/e2e/s7_hold.spec.ts. */
describe('the root rules in shell.css', () => {
  const css = readFileSync('src/shell/shell.css', 'utf8') // vitest runs from web/
  const rule = (selector: string): string => {
    const at = css.indexOf(`${selector} {`)
    expect(at, selector).toBeGreaterThan(-1)
    return css.slice(at, css.indexOf('}', at))
  }

  it('turns selection, the touch callout and the tap flash off on html and body', () => {
    const r = rule('html,\nbody')
    expect(r).toContain('user-select: none')
    expect(r).toContain('-webkit-user-select: none')
    expect(r).toContain('-webkit-touch-callout: none')
    expect(r).toContain('-webkit-tap-highlight-color: transparent')
    expect(r).toContain('touch-action: manipulation')
  })

  it('opts input, textarea and contenteditable back in, and nothing else', () => {
    const r = rule("input,\ntextarea,\n[contenteditable]:not([contenteditable='false'])")
    expect(r).toContain('user-select: text')
    expect(r).toContain('-webkit-user-select: text')
  })

  it('takes every browser gesture off the view and what is drawn over it', () => {
    expect(rule('.viewport')).toContain('touch-action: none')
  })
})

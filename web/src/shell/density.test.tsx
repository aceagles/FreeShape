import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fixture } from '../test/fixtures'
import { makeStore, stubFetch, tab } from '../test/fakes'
import { MODE_KEY } from './mode'
import { Shell } from './Shell'

// Card I9.15: the left column's density in Desktop mode. jsdom has no layout, so the heights are read the way the browser
// takes them: the stylesheets are put in the page and a row's minimum height is resolved through the shell's custom
// properties for the mode on it. The literals are what visualItemRect gave on the desktop program's MainWindow(engine=True)
// at 1440 x 900 with the bracket and the hinge open: features and mates 26, parts and instances 24, rollback row 12.
const DESKTOP = { feature: 26, part: 24, instance: 24, mate: 26, rollbackRow: 12, header: 28 }
const TOUCH = 44

const css = ['shell/shell.css', 'features/features.css', 'assembly/assembly.css'].map((f) => readFileSync(resolve(process.cwd(), 'src', f), 'utf8')).join('\n')

beforeEach(() => {
  const style = document.createElement('style')
  style.id = 'density-css'
  style.textContent = css
  document.head.append(style)
  // the tablet token the stylesheets read (tokenVars sets it as an inline custom property on .shell)
})
afterEach(() => {
  cleanup()
  document.getElementById('density-css')?.remove()
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

/** The tokens of a mode: the declarations of its rule in shell.css, merged over the touch ones the shell sets inline. */
function tokens(mode: 'desktop' | 'tablet'): Map<string, string> {
  const out = new Map<string, string>([['--feature-row', `${TOUCH}px`]])
  if (mode === 'desktop') {
    const block = /\.shell\[data-mode='desktop'\] \{([^}]*--d-row-feature[^}]*)\}/.exec(css)
    expect(block, 'the Desktop token block').not.toBeNull()
    for (const m of block![1]!.matchAll(/(--[\w-]+):\s*([^;]+);/g)) out.set(m[1]!, m[2]!.trim())
  }
  return out
}

/** The min-height a rule of the stylesheets gives `el`, as the number of px (var() resolved through the tokens). */
function minHeight(el: Element, mode: 'desktop' | 'tablet'): number {
  const t = tokens(mode)
  let found = ''
  for (const sheet of document.styleSheets) {
    for (const rule of sheet.cssRules) {
      if (!(rule instanceof CSSStyleRule) || rule.style.minHeight === '') continue
      const sel = rule.selectorText
      // a Desktop rule counts only in Desktop mode, and only when it matches this element
      if (sel.includes("[data-mode='desktop']") !== (mode === 'desktop') && sel.includes("[data-mode='desktop']")) continue
      const bare = sel.replace(/\.shell\[data-mode='desktop'\]\s*/g, '')
      try {
        if (el.matches(bare) || (sel.includes("[data-mode='desktop']") && el.matches(bare))) found = rule.style.minHeight
      } catch {
        // a selector jsdom cannot parse is not one of the row rules
      }
    }
  }
  const v = /^var\((--[\w-]+)\)$/.exec(found)
  return Number.parseFloat(v ? (t.get(v[1]!) ?? '') : found)
}

async function open(mode: 'desktop' | 'tablet', doc: 'bracket' | 'tree' | 'hinge') {
  window.localStorage.setItem(MODE_KEY, mode)
  const bytes = doc !== 'hinge' ? fixture(doc).bytes : (() => {
    const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
    return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
  })()
  stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes } : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  const tabs = doc !== 'hinge' ? [tab('T1', 'studio', 'Part Studio 1')] : [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')]
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: doc }, tabs, read_only: false, holder: null })
  if (doc !== 'hinge') await screen.findByRole('list', { name: 'Features' })
  else {
    fireEvent.click(await screen.findByRole('button', { name: 'hinge' }))
    await screen.findByTestId('instance-list')
  }
}

it('Desktop mode: a feature row, a part row, an instance row and a mate row have the desktop literal heights', async () => {
  await open('desktop', 'bracket')
  expect(screen.getByTestId('shell').dataset.mode).toBe('desktop')
  expect(minHeight(document.querySelector('.panel-row[data-feature]')!, 'desktop')).toBe(DESKTOP.feature)
  expect(minHeight(document.querySelector('.part-row')!, 'desktop')).toBe(DESKTOP.part)
  cleanup()
  await open('desktop', 'hinge')
  expect(minHeight(document.querySelector('.instance-row')!, 'desktop')).toBe(DESKTOP.instance)
  expect(minHeight(document.querySelector('.mate-row')!, 'desktop')).toBe(DESKTOP.mate)
})

it('Tablet mode: the same rows keep the 44 px touch height', async () => {
  await open('tablet', 'bracket')
  expect(screen.getByTestId('shell').dataset.mode).toBe('tablet')
  expect(minHeight(document.querySelector('.panel-row[data-feature]')!, 'tablet')).toBe(TOUCH)
  expect(minHeight(document.querySelector('.part-row')!, 'tablet')).toBe(TOUCH)
  cleanup()
  await open('tablet', 'hinge')
  expect(minHeight(document.querySelector('.instance-row')!, 'tablet')).toBe(TOUCH)
  expect(minHeight(document.querySelector('.mate-row')!, 'tablet')).toBe(TOUCH)
})

it('the Desktop token set holds the rollback bar at 8 px in a 12 px row, the font at 13 px and the icons at 18 and 14 px', () => {
  const t = tokens('desktop')
  expect(t.get('--d-rollback-row')).toBe(`${DESKTOP.rollbackRow}px`)
  expect(t.get('--d-rollback-bar')).toBe('8px')
  expect(t.get('--d-font')).toBe('13px')
  expect(t.get('--d-icon-feature')).toBe('18px')
  expect(t.get('--d-icon-part')).toBe('14px')
  expect(t.get('--d-row-pad-y')).toBe('3px')
  expect(t.get('--d-row-pad-x')).toBe('6px')
  expect(t.get('--d-row-radius')).toBe('3px')
  expect(t.get('--d-head-pad')).toBe('8px 10px 4px 10px')
  // card F18: the title row and a section header are 28 px; a header has the desktop's 1 px separator above it and no
  // other gap; the title row's buttons are 22 px; the eye and lock columns 24 px
  expect(t.get('--d-head-height')).toBe(`${DESKTOP.header}px`)
  expect(t.get('--d-head-gap')).toBe('0px')
  expect(t.get('--d-head-rule')).toBe('1px')
  expect(t.get('--d-header-button')).toBe('22px')
  expect(t.get('--d-eye-column')).toBe('24px')
})

const eyeStates = () => [...document.querySelectorAll<HTMLElement>('[data-feature-eye], [data-part-eye]')].map((e) => e.dataset.eye)

it('Desktop mode: the eye is shown on the hovered row and on hidden rows only', async () => {
  await open('desktop', 'tree')
  // every sketch of the fixture is hidden: its eye is shown (faint and crossed out by the stylesheet) with no hover
  const hidden = [...document.querySelectorAll<HTMLElement>('[data-feature-eye]')].filter((e) => e.closest('li')!.hasAttribute('data-hidden'))
  expect(hidden.length).toBeGreaterThan(0)
  for (const e of hidden) expect(e.dataset.eye).toBe('shown')
  // a part that is not hidden: its eye is off until its row is hovered, and off again when the mouse leaves
  const part = document.querySelector<HTMLElement>('[data-part-eye]')!
  const row = part.closest('li')!
  expect(row.hasAttribute('data-hidden')).toBe(false)
  expect(part.dataset.eye).toBe('off')
  fireEvent.mouseEnter(row)
  expect(part.dataset.eye).toBe('shown')
  fireEvent.mouseLeave(row)
  expect(part.dataset.eye).toBe('off')
})

it('Tablet mode: every eye is shown, as before', async () => {
  await open('tablet', 'tree')
  const states = eyeStates()
  expect(states.length).toBeGreaterThan(0)
  expect(states.every((s) => s === 'shown')).toBe(true)
  fireEvent.mouseEnter(document.querySelector('[data-part-eye]')!.closest('li')!)
  expect(eyeStates().every((s) => s === 'shown')).toBe(true)
})

it('Desktop mode, Assembly: the lock shows on a hovered or a fixed instance, the eye on a hovered or a hidden one', async () => {
  await open('desktop', 'hinge')
  const rows = [...document.querySelectorAll<HTMLElement>('.instance-row')]
  expect(rows.length).toBeGreaterThan(1)
  for (const r of rows) {
    const lock = r.querySelector<HTMLElement>('[data-instance-fixed]')!
    const eye = r.querySelector<HTMLElement>('[data-instance-eye]')!
    expect(lock.dataset.eye).toBe(r.hasAttribute('data-fixed') ? 'shown' : 'off')
    expect(eye.dataset.eye).toBe(r.hasAttribute('data-hidden') ? 'shown' : 'off')
  }
  const plain = rows.find((r) => !r.hasAttribute('data-fixed') && !r.hasAttribute('data-hidden'))!
  fireEvent.mouseEnter(plain)
  expect(plain.querySelector<HTMLElement>('[data-instance-fixed]')!.dataset.eye).toBe('shown')
  expect(plain.querySelector<HTMLElement>('[data-instance-eye]')!.dataset.eye).toBe('shown')
})

// Card F18: the blocks around the rows, as the desktop's panel has them.
const shown = (el: Element | null) => el !== null && getComputedStyle(el).display !== 'none'

it('Desktop mode, Assembly: "Instances (n)" is the title row, Delete is a small icon in it, mates show their name only', async () => {
  await open('desktop', 'hinge')
  const panel = screen.getByTestId('assembly-panel')
  const header = panel.querySelector('.panel-header')!
  expect(header.querySelector('.panel-title')!.textContent).toBe('Instances (3)')
  expect(panel.querySelector('.panel-body .parts-heading#instances-heading')).toBeNull()
  expect(within(panel).queryByText('Assembly')).toBeNull()
  // the first section header under the list is the Mates one, with no gap of its own above its separator
  expect([...panel.querySelectorAll('.panel-body .parts-heading')].map((h) => h.textContent)).toEqual(['Mates (2)'])
  // selecting an instance puts no Delete block under the list; the title row gets a small Delete button
  fireEvent.click(within(panel).getByRole('button', { name: /^Arm <1>/ }))
  expect(panel.querySelector('.instance-actions')).toBeNull()
  const del = within(header as HTMLElement).getByRole('button', { name: 'Delete Arm <1>' })
  expect(del.classList.contains('icon-button')).toBe(true)
  fireEvent.click(del)
  expect(screen.getByRole('dialog', { name: /Delete "Arm <1>"/ })).toBeTruthy()
  // a mate row: the name alone, the type and instances as its tooltip
  const mate = panel.querySelector<HTMLElement>('[data-mate-row]')!
  expect(shown(mate.querySelector('.row-name'))).toBe(true)
  expect(shown(mate.querySelector('.mate-detail'))).toBe(false)
  expect(mate.title).toBe('Fastened: Base plate <1> and Pin <1>')
  // an instance row's icon is its colour swatch, at the front; no cube
  const row = panel.querySelector('.instance-row .part-select')!
  expect(shown(row.querySelector(':scope > svg'))).toBe(false)
  expect(getComputedStyle(row.querySelector('.part-swatch')!).order).toBe('-1')
})

it('Tablet mode, Assembly: the "Assembly" title, the Instances heading, the Delete block and the mate detail are as before', async () => {
  await open('tablet', 'hinge')
  const panel = screen.getByTestId('assembly-panel')
  expect(panel.querySelector('.panel-header .panel-title')!.textContent).toBe('Assembly')
  expect(panel.querySelector('.panel-body #instances-heading')!.textContent).toBe('Instances (3)')
  fireEvent.click(within(panel).getByRole('button', { name: /^Arm <1>/ }))
  expect(panel.querySelector('.instance-actions .instance-delete')!.getAttribute('aria-label')).toBe('Delete Arm <1>')
  expect(panel.querySelector('.panel-header .instance-delete-icon')).toBeNull()
  expect(shown(panel.querySelector('[data-mate-row] .mate-detail'))).toBe(true)
  expect(shown(panel.querySelector('.instance-row .part-select > svg'))).toBe(true)
})

it('Desktop mode, Part Studio: a part row\'s icon is its colour swatch, at the front; no cube', async () => {
  await open('desktop', 'bracket')
  const row = document.querySelector('.part-row .part-select')!
  expect(shown(row.querySelector(':scope > svg'))).toBe(false)
  expect(getComputedStyle(row.querySelector('.part-swatch')!).order).toBe('-1')
})

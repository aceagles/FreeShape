import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { makeStore } from '../test/fakes'
import { TopBar } from './TopBar'

// Card F18: the full-screen button in the top bar, against a mocked Fullscreen API (jsdom has none). A real browser test
// of entering full screen is left out on purpose: headless browsers grant or refuse it unreliably.

let current: Element | null = null
const added: Array<[object, string]> = []
function define(target: object, name: string, value: unknown, getter = false) {
  Object.defineProperty(target, name, getter ? { configurable: true, get: value as () => unknown } : { configurable: true, writable: true, value })
  added.push([target, name])
}

afterEach(() => {
  cleanup()
  for (const [t, n] of added.splice(0)) delete (t as Record<string, unknown>)[n]
  current = null
  vi.restoreAllMocks()
})

/** The standard API (or WebKit's prefixed one): a request that the test settles by firing the change event itself. */
function mockApi(prefix: '' | 'webkit') {
  const el = document.documentElement
  const request = vi.fn(() => Promise.resolve())
  const exit = vi.fn(() => Promise.resolve())
  define(el, prefix ? 'webkitRequestFullscreen' : 'requestFullscreen', request)
  define(document, prefix ? 'webkitExitFullscreen' : 'exitFullscreen', exit)
  define(document, prefix ? 'webkitFullscreenElement' : 'fullscreenElement', () => current, true)
  const change = (on: boolean) =>
    act(() => {
      current = on ? el : null
      document.dispatchEvent(new Event(prefix ? 'webkitfullscreenchange' : 'fullscreenchange'))
    })
  return { request, exit, change }
}

function bar() {
  const { store } = makeStore()
  const noop = () => {}
  render(
    <TopBar
      store={store}
      currentTab={null}
      readOnly={false}
      saving={false}
      canUndo={false}
      canRedo={false}
      onUndo={noop}
      onRedo={noop}
      debugOpen={false}
      onSelectTab={noop}
      onAddTab={noop}
      onRenameTab={noop}
      onDeleteTab={noop}
      onBack={noop}
      onSave={noop}
      onToggleDebug={noop}
      menu={{ commands: {}, state: { readOnly: false, tabKind: null, sketchOpen: false } }}
      narrow={false}
    />,
  )
}

it('is not shown where the browser has no Fullscreen API', () => {
  expect('requestFullscreen' in document.documentElement).toBe(false)
  bar()
  expect(screen.queryByRole('button', { name: /full screen/i })).toBeNull()
})

it('is not shown when the page runs as an installed app (display-mode standalone, or iOS navigator.standalone)', () => {
  mockApi('')
  const mm = (q: string) => ({ matches: q === '(display-mode: standalone)' }) as MediaQueryList
  define(window, 'matchMedia', mm)
  bar()
  expect(screen.queryByRole('button', { name: /full screen/i })).toBeNull()
  cleanup()
  define(window, 'matchMedia', () => ({ matches: false }) as MediaQueryList)
  bar()
  expect(screen.getByRole('button', { name: 'Full screen' })).toBeTruthy()
  cleanup()
  define(navigator, 'standalone', true)
  bar()
  expect(screen.queryByRole('button', { name: /full screen/i })).toBeNull()
})

it('is not shown where the page may not go full screen (fullscreenEnabled false)', () => {
  mockApi('')
  define(document, 'fullscreenEnabled', false)
  bar()
  expect(screen.queryByRole('button', { name: /full screen/i })).toBeNull()
})

it('a click requests full screen for the document element; the change event flips its icon and name, and Esc flips them back', () => {
  const api = mockApi('')
  bar()
  const button = screen.getByRole('button', { name: 'Full screen' })
  const icon = () => button.querySelector('path')!.getAttribute('d')
  const enterIcon = icon()
  fireEvent.click(button)
  expect(api.request).toHaveBeenCalledTimes(1)
  expect(api.request.mock.contexts[0]).toBe(document.documentElement)
  // nothing changes until the browser says so
  expect(button.getAttribute('aria-label')).toBe('Full screen')
  api.change(true)
  expect(button.getAttribute('aria-label')).toBe('Exit full screen')
  expect(button.title).toBe('Exit full screen')
  expect(icon()).not.toBe(enterIcon)
  fireEvent.click(button)
  expect(api.exit).toHaveBeenCalledTimes(1)
  // left with Esc or the browser's own gesture: only the event comes
  api.change(false)
  expect(button.getAttribute('aria-label')).toBe('Full screen')
  expect(icon()).toBe(enterIcon)
})

it("WebKit's prefixed API is used where the standard one is missing", () => {
  const api = mockApi('webkit')
  bar()
  fireEvent.click(screen.getByRole('button', { name: 'Full screen' }))
  expect(api.request).toHaveBeenCalledTimes(1)
  api.change(true)
  expect(screen.getByRole('button', { name: 'Exit full screen' })).toBeTruthy()
})

it('a refused request (a rejected promise or a throw) does not throw and leaves the button as it was', async () => {
  const api = mockApi('')
  api.request.mockImplementationOnce(() => Promise.reject(new TypeError('refused')))
  bar()
  const button = screen.getByRole('button', { name: 'Full screen' })
  fireEvent.click(button)
  await act(async () => {})
  expect(button.getAttribute('aria-label')).toBe('Full screen')
  api.request.mockImplementationOnce(() => {
    throw new TypeError('refused')
  })
  expect(() => fireEvent.click(button)).not.toThrow()
  expect(button.getAttribute('aria-label')).toBe('Full screen')
})

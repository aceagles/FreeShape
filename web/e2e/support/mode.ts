import type { Mode } from '../../src/shell/mode.ts'

/** The localStorage key of the client's mode (src/shell/mode.ts). */
const MODE_KEY = 'freeshape.mode'

/** A Playwright storageState that stores `mode` for `origin`: the one place that puts every spec and shot in a known mode. */
export function modeState(origin: string, mode: Mode) {
  return { cookies: [] as never[], origins: [{ origin, localStorage: [{ name: MODE_KEY, value: mode }] }] }
}

/**
 * For a Desktop-mode spec: `test.use(desktopMode)` at the top of the file. The page starts with nothing stored and a
 * fine pointer with hover (no touch), so the client picks Desktop mode by itself.
 */
export const desktopMode = { storageState: { cookies: [], origins: [] }, hasTouch: false }

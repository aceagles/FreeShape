import { createContext, createElement, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

/** The client's mode: Desktop (mouse and keyboard) or Tablet (pen and finger). */
export type Mode = 'desktop' | 'tablet'

/** localStorage key holding the mode chosen on this device. */
export const MODE_KEY = 'freeshape.mode'
const DESKTOP_QUERY = '(pointer: fine) and (hover: hover)'

/**
 * The mode to start in: the one stored on this device; else Desktop when the primary pointer is fine and hovers; else
 * Tablet. Tablet also where matchMedia is missing or storage throws.
 */
export function initialMode(): Mode {
  try {
    const stored = window.localStorage.getItem(MODE_KEY)
    if (stored === 'desktop' || stored === 'tablet') return stored
  } catch {
    // storage blocked: fall through to the pointer test
  }
  try {
    return typeof window.matchMedia === 'function' && window.matchMedia(DESKTOP_QUERY).matches ? 'desktop' : 'tablet'
  } catch {
    return 'tablet'
  }
}

/** Stores the choice for this device; a storage that throws is ignored (the choice then lasts for the session). */
export function storeMode(mode: Mode): void {
  try {
    window.localStorage.setItem(MODE_KEY, mode)
  } catch {
    // keep the mode in memory only
  }
}

interface ModeApi {
  mode: Mode
  setMode: (mode: Mode) => void
}

const ModeContext = createContext<ModeApi>({ mode: 'tablet', setMode: () => {} })

/** Gives the mode to everything below it. The mode changes in place: children are not remounted. */
export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, set] = useState<Mode>(initialMode)
  const setMode = useCallback((next: Mode) => {
    storeMode(next)
    set(next)
  }, [])
  const value = useMemo(() => ({ mode, setMode }), [mode, setMode])
  return createElement(ModeContext.Provider, { value }, children)
}

/** The current mode. */
export function useMode(): Mode {
  return useContext(ModeContext).mode
}

/** The function that changes the mode (and stores it). */
export function useSetMode(): (mode: Mode) => void {
  return useContext(ModeContext).setMode
}

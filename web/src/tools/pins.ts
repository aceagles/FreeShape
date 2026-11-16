import type { StripKind } from './defs'

const KEY = 'freeshape.toolPins'

type Store = Pick<Storage, 'getItem' | 'setItem'>

function browserStorage(): Store | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

/**
 * The tools the user pinned to a strip. Pins belong to the user, not to a Document, and are kept in the browser's local
 * storage (milestone 1). Storage that is missing or refuses (a private window) leaves the pins for this page only.
 */
export class PinStore {
  private pins: Readonly<Record<StripKind, readonly string[]>> = { feature: [], sketch: [] }
  private readonly listeners = new Set<() => void>()

  constructor(private readonly storage: Store | null = browserStorage()) {
    try {
      const raw: unknown = JSON.parse(this.storage?.getItem(KEY) ?? 'null')
      if (typeof raw === 'object' && raw !== null) {
        const read = (k: StripKind): string[] => {
          const v = (raw as Record<string, unknown>)[k]
          return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
        }
        this.pins = { feature: read('feature'), sketch: read('sketch') }
      }
    } catch {
      // unreadable: start with none
    }
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** The pins of every strip. The same object until a pin changes. */
  readonly getSnapshot = (): Readonly<Record<StripKind, readonly string[]>> => this.pins

  /** Pin the tool, or unpin it when it is pinned. */
  toggle(strip: StripKind, id: string): void {
    const now = this.pins[strip]
    const next = now.includes(id) ? now.filter((x) => x !== id) : [...now, id]
    this.pins = { ...this.pins, [strip]: next }
    try {
      this.storage?.setItem(KEY, JSON.stringify(this.pins))
    } catch {
      // not kept: still pinned until the page closes
    }
    for (const l of this.listeners) l()
  }
}

let shared: PinStore | null = null
/** The one store of the page. */
export const userPins = (): PinStore => (shared ??= new PinStore())

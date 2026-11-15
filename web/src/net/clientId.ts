const KEY = 'freeshape.clientId'

/** 16 random bytes as 32 hex characters: letters and digits only, so the server accepts it as a client id. */
function randomId(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * This tab's client id. It is made once and kept in sessionStorage, so a reload of the tab, or a reconnect after
 * iPadOS dropped the socket, presents the same id and the server treats it as the same client. When the storage is
 * missing or refuses (private mode), an id lasts as long as the page does.
 */
export function getClientId(storage: Pick<Storage, 'getItem' | 'setItem'> | null = safeStorage()): string {
  try {
    const kept = storage?.getItem(KEY)
    if (kept && /^[A-Za-z0-9_-]{8,64}$/.test(kept)) return kept
  } catch {
    // fall through to a new id
  }
  const id = randomId()
  try {
    storage?.setItem(KEY, id)
  } catch {
    // the id still works for this page
  }
  return id
}

function safeStorage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

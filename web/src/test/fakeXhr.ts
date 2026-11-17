import { vi } from 'vitest'

/** A stand-in for XMLHttpRequest that a test drives by hand: it records the request and the test says how far the body is and what the server answers. */
export class FakeXhr {
  static all: FakeXhr[] = []
  method = ''
  url = ''
  headers: Record<string, string> = {}
  body: unknown = undefined
  sent = false
  aborted = false
  status = 0
  responseText = ''
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  constructor() {
    FakeXhr.all.push(this)
  }
  open(method: string, url: string): void {
    this.method = method
    this.url = url
  }
  setRequestHeader(k: string, v: string): void {
    this.headers[k] = v
  }
  send(body: unknown): void {
    this.sent = true
    this.body = body
  }
  abort(): void {
    this.aborted = true
    this.onabort?.()
  }
  /** The browser says `loaded` of `total` bytes have gone out. */
  progress(loaded: number, total: number): void {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total })
  }
  /** The server answers `status` with a JSON body. */
  reply(status: number, json: unknown): void {
    this.status = status
    this.responseText = JSON.stringify(json)
    this.onload?.()
  }
}

/** Puts FakeXhr in place of XMLHttpRequest (undo with vi.unstubAllGlobals) and forgets the requests of the test before. */
export function installFakeXhr(): typeof FakeXhr {
  FakeXhr.all = []
  vi.stubGlobal('XMLHttpRequest', FakeXhr)
  return FakeXhr
}

import { ApiError } from './api'

/** What `uploadFile` needs. Nothing here is specific to one kind of file: the route, the query and the reply are the caller's. */
export interface UploadOptions {
  /** The route, without a query. */
  url: string
  /** Query values to add to the route (`name` is the file's name when the caller passes it). */
  query?: Record<string, string>
  /** The largest file the server takes, in bytes; a larger file is refused before any request. Null or absent: no check here, the server decides. */
  limit?: number | null
  /** Called as the body goes out, with the bytes sent so far and the total. */
  onProgress?: (sent: number, total: number) => void
  /** Aborting it cancels the request; the promise rejects with an AbortError. */
  signal?: AbortSignal
  headers?: Record<string, string>
}

/** A file over the limit the caller gave; no request was made. */
export class UploadTooLarge extends Error {
  constructor(
    readonly size: number,
    readonly limit: number,
    readonly fileName: string,
  ) {
    super(`${fileName} is ${size} bytes; the server takes at most ${limit}.`)
    this.name = 'UploadTooLarge'
  }
}

const abortError = (): DOMException => new DOMException('The upload was cancelled.', 'AbortError')

/** The route with its query string. */
export function uploadUrl(url: string, query: Record<string, string> = {}): string {
  const q = new URLSearchParams(query).toString()
  return q === '' ? url : `${url}${url.includes('?') ? '&' : '?'}${q}`
}

/**
 * POSTs `file` as application/octet-stream with progress (XMLHttpRequest: fetch reports no upload progress), and resolves with the
 * JSON reply. A refusal rejects with an ApiError carrying the server's own text (status 0 when the server did not answer); a file
 * over `limit` rejects with UploadTooLarge before a request is made; an aborted `signal` rejects with an AbortError.
 */
export function uploadFile(file: File, opts: UploadOptions): Promise<unknown> {
  if (opts.limit != null && file.size > opts.limit) return Promise.reject(new UploadTooLarge(file.size, opts.limit, file.name))
  if (opts.signal?.aborted) return Promise.reject(abortError())
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const stop = () => xhr.abort()
    opts.signal?.addEventListener('abort', stop, { once: true })
    const done = () => opts.signal?.removeEventListener('abort', stop)
    xhr.open('POST', uploadUrl(opts.url, opts.query))
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    for (const [k, v] of Object.entries(opts.headers ?? {})) xhr.setRequestHeader(k, v)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts.onProgress?.(e.loaded, e.total)
    }
    xhr.onload = () => {
      done()
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // not JSON: the status below has to do
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body)
      const obj = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
      reject(new ApiError(xhr.status, typeof obj.error === 'string' ? obj.error : `The server answered ${xhr.status}.`, obj))
    }
    xhr.onerror = () => {
      done()
      reject(new ApiError(0, 'The server did not answer. Check the connection and try again.'))
    }
    xhr.onabort = () => {
      done()
      reject(abortError())
    }
    xhr.send(file)
  })
}

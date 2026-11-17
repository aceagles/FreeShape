import { request, tabPath, seg } from '../net/api'
import { uploadFile } from '../net/upload'

/** The largest image the server takes (freeshape/serve.py MAX_IMAGE: 20 MiB); a larger file is refused here before any request. */
export const CANVAS_IMAGE_LIMIT = 20 * 1024 * 1024

/** What the device picker offers for a Canvas: PNG and JPEG (the server refuses anything else, by the file's bytes, whatever its name). */
export const CANVAS_ACCEPT = 'image/png,image/jpeg,.png,.jpg,.jpeg'

/** What an upload answers (card I12.2): the blob key to open the dialog with, and the picture's size. */
export interface CanvasUpload {
  document: string
  tab: string
  blob: string
  filename: string
  format: 'png' | 'jpeg'
  pixels: [number, number]
  /** The default size in mm. */
  width: number
  height: number
}

/**
 * Sends a PNG or JPEG to the tab (card I12.2's `POST .../images`), as application/octet-stream with progress. The upload belongs
 * to `client`: only that client can open a dialog on it, and only it can read it back before a canvas names it. A refusal is an
 * ApiError with the server's text (415 not a whole PNG or JPEG, 413 too big, 423 read-only), a file over the limit is an
 * UploadTooLarge before any request, an abort is an AbortError.
 */
export async function uploadCanvasImage(
  doc: string,
  tab: string,
  file: File,
  client: string,
  o: { onProgress?: (sent: number, total: number) => void; signal?: AbortSignal } = {},
): Promise<CanvasUpload> {
  return (await uploadFile(file, { url: `${tabPath(doc, tab)}/images`, query: { name: file.name }, limit: CANVAS_IMAGE_LIMIT, headers: { 'X-Client-Id': client }, ...o })) as CanvasUpload
}

/**
 * A canvas's picture (`GET .../images/<blob>`). The client id is sent always: an image this client uploaded and has not yet
 * placed (the dialog's preview) is served only with it, and a placed one is served either way. Fetched here, then decoded from a
 * Blob, because an image element or a texture URL cannot send the header.
 */
export async function fetchCanvasImage(doc: string, tab: string, blob: string, client: string): Promise<Blob> {
  const res = await request('GET', `${tabPath(doc, tab)}/images/${seg(blob)}`, { headers: { 'X-Client-Id': client } })
  return res.blob()
}

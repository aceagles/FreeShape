/** Why a payload was refused. `code` is stable; `message` is for people. */
export type WireErrorCode =
  | 'too-short' // fewer than the 8 prefix bytes
  | 'bad-magic' // does not start with FSW1
  | 'truncated-header' // the header length runs past the end
  | 'bad-header' // header is not the JSON object {version, buffers_size, root}
  | 'truncated-buffers' // the buffer region is shorter than the header says
  | 'trailing-bytes' // bytes after the buffer region
  | 'bad-node' // a node inside the header breaks the format
  | 'unsupported' // valid, but this reader does not handle it (dtype, endianness)
  | 'bad-structure' // valid wire, but not the outputs shape the client expects

export class WireError extends Error {
  readonly code: WireErrorCode

  constructor(code: WireErrorCode, message: string) {
    super(message)
    this.name = 'WireError'
    this.code = code
  }
}

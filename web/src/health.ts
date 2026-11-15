export interface Health {
  ok: boolean
  version: string
}

/** Fetch /health from the server that hosts this page. Rejects when it does not answer with a version. */
export async function fetchHealth(fetcher: typeof fetch = fetch): Promise<Health> {
  const res = await fetcher('/health')
  if (!res.ok) throw new Error(`/health answered ${res.status}`)
  const body: unknown = await res.json()
  if (typeof body !== 'object' || body === null) throw new Error('/health did not answer with an object')
  const { ok, version } = body as { ok?: unknown; version?: unknown }
  if (typeof version !== 'string') throw new Error('/health has no version')
  return { ok: ok === true, version }
}

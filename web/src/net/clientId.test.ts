import { expect, it } from 'vitest'
import { getClientId } from './clientId'

function memory(): Pick<Storage, 'getItem' | 'setItem'> & { data: Map<string, string> } {
  const data = new Map<string, string>()
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) }
}

it('makes an id the server accepts and keeps it in the storage', () => {
  const storage = memory()
  const id = getClientId(storage)
  expect(id).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  expect([...storage.data.values()]).toEqual([id])
})

it('returns the same id after a reload of the tab', () => {
  const storage = memory()
  expect(getClientId(storage)).toBe(getClientId(storage))
})

it('works in this page when the storage refuses', () => {
  const refusing = {
    getItem: () => {
      throw new Error('denied')
    },
    setItem: () => {
      throw new Error('denied')
    },
  }
  expect(getClientId(refusing)).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  expect(getClientId(null)).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
})

it('uses sessionStorage by default, so a new store in the same tab gets the same id', () => {
  window.sessionStorage.clear()
  expect(getClientId()).toBe(getClientId())
})

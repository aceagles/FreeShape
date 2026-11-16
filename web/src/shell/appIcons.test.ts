import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import { color } from '../theme/tokens'

// Card F21: the web app manifest, its icons and the tags in index.html (what the home screen reads). What iPadOS does
// with them cannot be tested here; the hand check is in the card's notes.

const pub = (f: string) => resolve(__dirname, '../../public', f)
const manifest = JSON.parse(readFileSync(pub('manifest.webmanifest'), 'utf8')) as Record<string, unknown> & {
  icons: Array<{ src: string; sizes: string; type: string; purpose: string }>
}
const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf8')

function pngSize(file: string): [number, number] {
  const b = readFileSync(file)
  expect(b.subarray(1, 4).toString('latin1')).toBe('PNG')
  return [b.readUInt32BE(16), b.readUInt32BE(20)]
}

it('the manifest names the app, opens at the root without browser chrome and uses the design tokens', () => {
  expect(manifest.name).toBe('FreeShape')
  expect(manifest.short_name).toBeTruthy()
  expect(manifest.display).toBe('standalone')
  expect(manifest.start_url).toBe('./')
  expect(manifest.scope).toBe('./')
  expect(manifest.orientation).toBe('any')
  expect(manifest.background_color).toBe(color.white)
  expect(manifest.theme_color).toBe(color.white)
})

it('every icon the manifest names exists at its declared size, with a 192, a 512 and a maskable one', () => {
  for (const icon of manifest.icons) {
    const [w, h] = icon.sizes.split('x').map(Number)
    expect(pngSize(pub(icon.src))).toEqual([w, h])
    expect(icon.type).toBe('image/png')
  }
  const sizes = manifest.icons.filter((i) => i.purpose === 'any').map((i) => i.sizes)
  expect(sizes).toEqual(expect.arrayContaining(['192x192', '512x512']))
  expect(manifest.icons.some((i) => i.purpose === 'maskable' && i.sizes === '512x512')).toBe(true)
})

it('index.html links the manifest with credentials and carries the apple tags and a 180 px touch icon', () => {
  expect(html).toMatch(/<link rel="manifest" href="\/manifest\.webmanifest" crossorigin="use-credentials"/)
  for (const name of ['apple-mobile-web-app-capable', 'mobile-web-app-capable']) {
    expect(html).toContain(`<meta name="${name}" content="yes"`)
  }
  expect(html).toContain('name="apple-mobile-web-app-status-bar-style" content="default"')
  expect(html).toContain('name="apple-mobile-web-app-title" content="FreeShape"')
  expect(html).toContain(`name="theme-color" content="${color.white}"`)
  expect(html).toContain('viewport-fit=cover')
  expect(html).toMatch(/<link rel="apple-touch-icon" sizes="180x180" href="\/apple-touch-icon\.png"/)
  expect(pngSize(pub('apple-touch-icon.png'))).toEqual([180, 180])
})

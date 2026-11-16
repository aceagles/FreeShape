// Renders the home-screen icons of the Browser client from the desktop program's logo (freeshape/assets/logo.svg).
//
//   node scripts/web/app_icons.mjs            writes web/public/*.png
//   node scripts/web/app_icons.mjs --check    writes nothing; exits 1 when a committed PNG differs
//
// Needs Playwright's browser: run it in mcr.microsoft.com/playwright:v1.64.1-noble with the repo mounted (the PNG bytes
// depend on that renderer, so --check is only meaningful there). The icons:
//   icon-192.png, icon-512.png   the manifest's "any" icons: logo on transparent, 6% margin
//   icon-maskable-512.png        the manifest's "maskable" icon: logo inside the inner 56% (the safe zone is a circle
//                                of 80%; the logo's corners stay within it) on the white of the top bar
//   apple-touch-icon.png         180 px, solid white (iOS paints transparent areas black), logo at 80%
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const { chromium } = createRequire(resolve(root, 'web/package.json'))('@playwright/test')
const svg = readFileSync(resolve(root, 'freeshape/assets/logo.svg'), 'utf8')
const WHITE = '#ffffff' // color.white in web/src/theme/tokens.ts: the top bar and the page background

const ICONS = [
  { file: 'icon-192.png', size: 192, scale: 0.88, bg: null },
  { file: 'icon-512.png', size: 512, scale: 0.88, bg: null },
  { file: 'icon-maskable-512.png', size: 512, scale: 0.56, bg: WHITE },
  { file: 'apple-touch-icon.png', size: 180, scale: 0.8, bg: WHITE },
]

const page = ({ size, scale, bg }) =>
  `<!doctype html><html><body style="margin:0;width:${size}px;height:${size}px;background:${bg ?? 'transparent'};display:flex;align-items:center;justify-content:center">` +
  `<img alt="" style="width:${Math.round(size * scale)}px;height:${Math.round(size * scale)}px" src="data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}"></body></html>`

const check = process.argv.includes('--check')
const browser = await chromium.launch()
let drift = 0
for (const icon of ICONS) {
  const ctx = await browser.newContext({ viewport: { width: icon.size, height: icon.size }, deviceScaleFactor: 1 })
  const p = await ctx.newPage()
  await p.setContent(page(icon))
  await p.waitForFunction(() => document.images[0].complete)
  const png = await p.screenshot({ omitBackground: icon.bg === null, type: 'png' })
  await ctx.close()
  const out = resolve(root, 'web/public', icon.file)
  if (check) {
    if (!existsSync(out) || !readFileSync(out).equals(png)) {
      console.error(`web/public/${icon.file} differs from the logo; run node scripts/web/app_icons.mjs`)
      drift++
    }
  } else {
    writeFileSync(out, png)
    console.log(`wrote web/public/${icon.file}`)
  }
}
await browser.close()
process.exit(drift ? 1 : 0)

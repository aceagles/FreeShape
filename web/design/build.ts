// Writes web/design/layout.html. Run with `npm run design:build` (Node 22.18 or later runs .ts).
import { writeFileSync } from 'node:fs'
import { buildLayoutPage } from '../src/theme/layoutPage.ts'

writeFileSync(new URL('./layout.html', import.meta.url), buildLayoutPage())

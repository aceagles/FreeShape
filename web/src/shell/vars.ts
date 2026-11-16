import type { CSSProperties } from 'react'
import { color, fontFamily, fontSize, radius, size, space } from '../theme/tokens'

/** The design tokens as CSS custom properties, set on the shell root so shell.css and the layout page share one source. */
export function tokenVars(): CSSProperties {
  const vars: Record<string, string> = { '--font': fontFamily }
  for (const [k, v] of Object.entries(color)) vars[`--c-${k}`] = v
  for (const [k, v] of Object.entries(space)) vars[`--sp-${k.slice(1)}`] = `${v}px`
  for (const [k, v] of Object.entries(radius)) vars[`--r-${k}`] = `${v}px`
  for (const [k, v] of Object.entries(fontSize)) vars[`--fs-${k}`] = `${v}px`
  vars['--touch'] = `${size.touchMin}px`
  vars['--top-bar'] = `${size.topBar}px`
  vars['--tool-strip'] = `${size.toolStrip}px`
  vars['--tool-w'] = `${size.toolButton.width}px`
  vars['--tool-h'] = `${size.toolButton.height}px`
  vars['--group-row'] = `${size.groupRow}px`
  vars['--pin-button'] = `${size.pinButton}px`
  vars['--panel-header'] = `${size.panelHeader}px`
  vars['--dialog-header'] = `${size.dialogHeader}px`
  vars['--feature-row'] = `${size.featureRow}px`
  vars['--status-dot'] = `${size.statusDot}px`
  vars['--inset'] = `${size.viewportInset}px`
  vars['--icon'] = `${size.icon}px`
  return vars as CSSProperties
}

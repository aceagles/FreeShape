import { chordFor } from '../input/bindings'
import { MATE_IDS, RELATION_IDS, typeName } from '../tools/defs'
import type { DesktopEntry, DesktopLayout } from '../tools/defs'
import type { AssemblyTypes } from '../assembly/strip'
import type { ToolbarCommand } from '../tools/DesktopToolbar'
import { DESKTOP_ONLY_NOTE } from '../tools/ToolStrip'
import type { ToolState } from '../tools/useToolTypes'
import { commandOf, DESKTOP_ONLY, isWired, MENU_BAR } from './menus'
import type { Commands, MenuItem, MenuState } from './menus'
import { ASSEMBLY_TEXT, MATE_WORDS, SKETCH_TEXT, STUDIO_TEXT } from './paletteText'
import type { PaletteText } from './paletteText'
import type { Searchable } from './paletteSearch'

/** One line of the Search tools palette: a tool of the workspace's toolbar or a menu command. */
export interface PaletteEntry extends Searchable {
  /** Unique in the list: `tool:<id>` or the menu item's command. */
  id: string
  /** The icon file's name (src/icons), or null for a menu command. */
  icon: string | null
  /** The key that runs it, as the toolbar tooltip or the menu shows it. */
  chord?: string
  /** Why it cannot run now; '' when it can. The text is the toolbar's tooltip or the menu's own. */
  disabled: string
  run: () => void
}

export type PaletteWorkspace = 'studio' | 'sketch' | 'assembly'

/** What the shell hands the palette: the toolbar on screen (as DesktopToolbar gets it) and the menus. */
export interface PaletteEnv {
  /** The workspace whose toolbar is on screen, or null with no tab (only the menus are listed). */
  workspace: PaletteWorkspace | null
  layout: DesktopLayout | null
  stateOf: (id: string) => ToolState
  commands: Readonly<Record<string, ToolbarCommand>>
  /** The feature buttons are off (a dialog is open): DesktopToolbar's `busy`. */
  busy?: boolean
  onStart: (id: string) => void
  assemblyTypes: AssemblyTypes | null
  /** The menus' commands (Shell `commands`) and what their enabled rules read. */
  menuCommands: Commands
  menuState: MenuState
}

/** The group an entry the desktop's palette does not list is filed under. */
const DEFAULT_GROUP: Readonly<Record<PaletteWorkspace, string>> = { studio: 'Feature', sketch: 'Sketch', assembly: 'Assembly' }
/** The reason where a feature button is off because a dialog is open (the desktop's own words for Interference detection). */
const FINISH_FIRST = 'Finish or cancel the open feature first'
/** The reason where a button is on in the toolbar's rules but off now, and the toolbar's tooltip says only its name. */
const NOT_NOW = 'Not available now'

/** The palette's words for a tool: the desktop's table, or for a mate or relation the server's label and help, else the label. */
function textOf(workspace: PaletteWorkspace, id: string, label: string, types: AssemblyTypes | null): PaletteText {
  if (workspace === 'assembly') {
    const mate = /^m_(.+)$/.exec(id)
    const rel = /^r_(.+)$/.exec(id)
    if (mate && MATE_IDS.includes(mate[1]!)) {
      const info = types?.mates.find((m) => m.id === mate[1])
      const name = info?.label ?? typeName(mate[1]!)
      return { name, description: info === undefined ? `Add a ${name} mate.` : `Add a ${name} mate — ${info.help}.`, keywords: `mate ${MATE_WORDS[mate[1]!] ?? 'joint'}`, group: 'Mate' }
    }
    if (rel && RELATION_IDS.includes(rel[1]!)) {
      const info = types?.relations.find((r) => r.id === rel[1])
      const name = `${info?.label ?? typeName(rel[1]!)} relation`
      return { name, description: info === undefined ? `${name}.` : `${info.help}.`, keywords: 'relation mate connector link', group: 'Relation' }
    }
  }
  const table = workspace === 'studio' ? STUDIO_TEXT : workspace === 'sketch' ? SKETCH_TEXT : ASSEMBLY_TEXT
  return table[id] ?? { name: label, description: '', keywords: '', group: DEFAULT_GROUP[workspace] }
}

/** A toolbar button as a palette entry, deciding what it can do as DesktopToolbar draws it. */
function toolbarEntry(e: Exclude<DesktopEntry, { kind: 'separator' }>, env: PaletteEnv, workspace: PaletteWorkspace): PaletteEntry {
  const id = e.tool.id
  let disabled = ''
  let run: () => void = () => {}
  if (e.kind === 'command') {
    const cmd = env.commands[id]
    if (cmd === undefined) disabled = DESKTOP_ONLY_NOTE
    else if (cmd.enabled === false) disabled = cmd.note ?? NOT_NOW
    else run = cmd.run
  } else {
    const state: ToolState = env.busy === true ? 'off' : env.stateOf(id)
    if (env.busy === true) disabled = FINISH_FIRST
    else if (state === 'desktop' || state === 'off') disabled = DESKTOP_ONLY_NOTE
    else if (state === 'dimmed') disabled = 'Pick something it can use first'
    else run = () => env.onStart(id)
  }
  const text = textOf(workspace, id, e.tool.label, env.assemblyTypes)
  return { id: `tool:${id}`, ...text, icon: e.tool.icon, chord: e.key, disabled, run }
}

/** The menus' commands the toolbar already lists under another name. */
const ON_TOOLBAR: ReadonlySet<string> = new Set(['view.fit'])

/** Every menu command, one entry each (a submenu's items by "<submenu>: <item>"), with the menu's reason where it cannot run. */
function menuEntries(env: PaletteEnv, onToolbar: boolean): PaletteEntry[] {
  const out: PaletteEntry[] = []
  const add = (item: MenuItem, name: string, group: string) => {
    if (item.submenu === true && item.items === undefined) {
      out.push({ id: item.command, name, description: '', keywords: '', group, icon: null, chord: item.chord, disabled: item.note ?? DESKTOP_ONLY, run: () => {} })
      return
    }
    const run = commandOf(item, env.menuCommands)
    let disabled = ''
    if (!isWired(item, env.menuCommands) || run === undefined) disabled = item.note ?? DESKTOP_ONLY
    else if (item.enabled?.(env.menuState) === false) disabled = env.menuState.readOnly ? 'This Document is open read-only' : NOT_NOW
    out.push({ id: item.command, name, description: '', keywords: '', group, icon: null, chord: item.chord ?? chordFor(item.command), disabled, run: run ?? (() => {}) })
  }
  for (const menu of MENU_BAR) {
    for (const entry of menu.entries) {
      if (entry.kind === 'separator' || (onToolbar && ON_TOOLBAR.has(entry.command))) continue
      if (entry.items !== undefined) {
        for (const sub of entry.items) if (sub.kind !== 'separator') add(sub, `${entry.label}: ${sub.label}`, menu.label)
      } else add(entry, entry.label, menu.label)
    }
  }
  return out
}

/**
 * The entries of the palette: every tool of the toolbar on screen in its order (a feature toolbar, the sketch's or the
 * Assembly's), then every menu command. A tool or command that cannot run is listed with the reason the toolbar or the menu
 * gives for it.
 */
export function paletteEntries(env: PaletteEnv): PaletteEntry[] {
  const tools: PaletteEntry[] = []
  if (env.workspace !== null && env.layout !== null) {
    for (const e of [...env.layout.items, ...env.layout.trailing]) if (e.kind !== 'separator') tools.push(toolbarEntry(e, env, env.workspace))
  }
  return [...tools, ...menuEntries(env, tools.length > 0)]
}

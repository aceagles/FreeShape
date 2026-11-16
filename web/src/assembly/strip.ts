import { useEffect, useState } from 'react'
import { fetchAssemblyTypes } from '../net/api'
import type { AssemblyTypesReply } from '../net/api'
import { MATE_IDS, RELATION_IDS, typeName } from '../tools/defs'
import type { StripDef, ToolDef } from '../tools/defs'
import { space } from '../theme/tokens'
import type { ToolbarCommand } from '../tools/DesktopToolbar'
import type { ToolState } from '../tools/useToolTypes'

/** Why Insert and the mate buttons are off until the authoring cards (I10.1 Insert, I10.6 the mates) land. */
export const S10_NOTE = 'Comes with assembly authoring (S10)'

/** The mate and relation types of the Assembly tab on screen, from the server; null until it has answered (or when it cannot). */
export type AssemblyTypes = Pick<AssemblyTypesReply, 'mates' | 'relations'>

/**
 * The types of an Assembly tab, asked of the server once per tab. `tab` null (no Assembly on screen) asks nothing. A failed
 * answer leaves null: the toolbar then shows the types by their ids (defs.ts MATE_IDS), without the server's labels.
 */
export function useAssemblyTypes(documentId: string | null, tab: string | null): AssemblyTypes | null {
  const [got, setGot] = useState<{ key: string; types: AssemblyTypes } | null>(null)
  const key = `${documentId}/${tab}`
  useEffect(() => {
    if (documentId === null || tab === null) return
    const ctl = new AbortController()
    fetchAssemblyTypes(documentId, tab, ctl.signal).then(
      (r) => setGot({ key, types: { mates: r.mates, relations: r.relations } }),
      () => {
        // no answer: the toolbar keeps the ids' names
      },
    )
    return () => ctl.abort()
  }, [documentId, tab, key])
  return got !== null && got.key === key ? got.types : null
}

/** The label and help the server gives a type, by tool id (`m_<id>`, `r_<id>`). */
export function typeInfo(types: AssemblyTypes | null): Map<string, { label: string; help: string }> {
  const out = new Map<string, { label: string; help: string }>()
  for (const m of types?.mates ?? []) out.set(`m_${m.id}`, { label: m.label, help: m.help })
  for (const r of types?.relations ?? []) out.set(`r_${r.id}`, { label: r.label, help: r.help })
  return out
}

const tool = (id: string, label: string, icon: string = id): ToolDef => ({ id, label, icon })

/**
 * The Assembly strip of Tablet mode: Insert, a Mate group with the types of the route, a Relation group, Move and Fix, Fit.
 * The types come from the route; before it has answered the groups hold the ids' names (see useAssemblyTypes).
 */
export function assemblyStrip(types: AssemblyTypes | null): StripDef[] {
  const named = typeInfo(types)
  const kind = (prefix: 'm' | 'r', ids: readonly string[]): ToolDef[] =>
    (types === null ? ids : (prefix === 'm' ? types.mates : types.relations).map((x) => x.id)).map((id) =>
      tool(`${prefix}_${id}`, named.get(`${prefix}_${id}`)?.label ?? typeName(id)),
    )
  return [
    { kind: 'tool', tool: tool('insert', 'Insert') },
    { kind: 'group', key: 'mate', name: 'Mate', gap: space.s10, tools: kind('m', MATE_IDS) },
    { kind: 'group', key: 'relation', name: 'Relation', tools: kind('r', RELATION_IDS) },
    { kind: 'tool', tool: tool('move', 'Move'), gap: space.s10 },
    { kind: 'tool', tool: tool('asm_fix', 'Fix') },
    { kind: 'tool', tool: tool('fit', 'Fit'), gap: space.s10 },
  ]
}

/** Which tools of the Assembly strips are off until a later card, and why. A mate or Insert waits for S10; the rest are the desktop's. */
export const isAuthoringTool = (id: string): boolean => id === 'insert' || id.startsWith('m_')

/** The instances an Assembly selection holds: each instance with anything selected on it, in the order first met. */
export const instancesOf = (items: readonly { instance?: string }[]): string[] => [...new Set(items.flatMap((i) => (i.instance !== undefined && i.instance !== '' ? [i.instance] : [])))]

/** What the shell gives the Assembly toolbar to do its commands. */
export interface AssemblyToolbarEnv {
  types: AssemblyTypes | null
  /** The Assembly on screen, or null while it loads. */
  instances: readonly { id: string; name: string; fixed: boolean; hidden: boolean }[]
  /** The instances with anything selected on them. */
  selected: readonly string[]
  /** The Document is read-only here. */
  readOnly: boolean
  undo: () => void
  redo: () => void
  setFixedAll: (instances: readonly string[], fixed: boolean) => Promise<void>
  view: (command: 'fit' | 'iso') => void
  /** Open the Export dialog (the Desktop row's Export button). */
  openExport: () => void
  /** Tell the person why a button did nothing. */
  say: (title: string, text: string) => void
}

/**
 * The Assembly toolbar's buttons the client can do: Undo, Redo, Fix, Move, Export, Fit and Iso (Iso has a place in the Desktop row
 * only), and Insert and the mate types, which are there but off until S10 enables them. Anything else of the row is not
 * here, so the row disables it as "Desktop only for now". Both modes use the same commands.
 */
export function assemblyCommands(env: AssemblyToolbarEnv): Record<string, ToolbarCommand> {
  const named = typeInfo(env.types)
  const chosen = env.instances.filter((i) => env.selected.includes(i.id))
  const out: Record<string, ToolbarCommand> = {
    undo: { run: env.undo, enabled: !env.readOnly },
    redo: { run: env.redo, enabled: !env.readOnly },
    fit: { run: () => env.view('fit') },
    iso: { run: () => env.view('iso') },
    export: { run: env.openExport },
    insert: { run: () => {}, enabled: false, note: S10_NOTE },
    asm_fix: {
      enabled: !env.readOnly,
      // every selected instance fixed: free them; otherwise fix the ones that are free
      pressed: chosen.length > 0 && chosen.every((i) => i.fixed),
      run: () => {
        if (chosen.length === 0) return env.say('Fix', 'Select an instance to fix or free.')
        const fix = !chosen.every((i) => i.fixed)
        void env.setFixedAll(chosen.filter((i) => i.fixed !== fix).map((i) => i.id), fix)
      },
    },
    move: {
      enabled: !env.readOnly,
      run: () => {
        // the gizmo shows on the one free instance that is selected; this says why it is not there
        if (chosen.length === 0) return env.say('Move', 'Select an instance to move.')
        if (chosen.length > 1) return env.say('Move', 'Select one instance to move.')
        const [one] = chosen
        if (one!.fixed) env.say('Move', `${one!.name} is fixed. Free it first, then move it.`)
        else if (one!.hidden) env.say('Move', `${one!.name} is hidden. Show it first, then move it.`)
      },
    },
  }
  for (const id of MATE_IDS) {
    const info = named.get(`m_${id}`)
    out[`m_${id}`] = { run: () => {}, enabled: false, note: S10_NOTE, label: info === undefined ? undefined : `${info.label} mate`, tip: info === undefined ? undefined : `${info.label} mate — ${info.help}` }
  }
  return out
}

/** The state of a tool of the Tablet Assembly strip: Insert and the mates wait for S10, the relations are the desktop's. */
export function assemblyToolState(id: string, commands: Readonly<Record<string, ToolbarCommand>>): ToolState {
  if (isAuthoringTool(id)) return 'off'
  const cmd = commands[id]
  return cmd === undefined ? 'desktop' : cmd.enabled === false ? 'off' : 'ok'
}

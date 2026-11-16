import type { SelectionItem, SelectionStore } from '../selection/store'
import { DEFAULT_PLANES } from '../viewport/scene'
import type { TabView, ViewState } from '../viewport/visibility'
import type { AssemblyOutputs, StudioOutputs } from '../wire'

/**
 * The desktop's visibility keys and the View menu's toggles (card I9.8), one function per command: a key and its menu item
 * call the same one. Read from freeshape/ui/mainwindow.py `VIS_KEYS`, `_vis_key`, `_vis_targets` and the `_svis_*` / `_avis_*`
 * methods. What the server keeps (a part's or an instance's eye, a sketch's, a Context's) is sent as an edit; the rest is the
 * tab's client view state (viewport/visibility.ts).
 */
export interface VisibilityContext {
  /** The kind of the tab on screen, or null with no tab. */
  tabKind: string | null
  readOnly: boolean
  /** A sketch is open: the sketch tools own these letters. */
  sketchOpen: boolean
  /** A feature dialog is open: its picks are the selection, so only what is under the pointer counts as a target. */
  dialogOpen: boolean
  view: TabView
  selection: SelectionStore
  studio: StudioOutputs | null
  assembly: AssemblyOutputs | null
  /** The feature selected in the feature list, or null. */
  selectedFeature: string | null
  /** One edit of the tab on screen, as `ConnectionStore.edit` sends it; rejects when the server refuses. */
  send: (edit: Record<string, unknown>) => Promise<unknown>
  /** A refusal, in plain words. */
  fail: (title: string, error: unknown) => void
}

/** What Y, Shift+I and Shift+T act on: part ids (instance ids in an Assembly) and the ids of sketches, planes and axes. */
export interface VisibilityTargets {
  parts: string[]
  features: string[]
}

const FEATURE_EYES: ReadonlySet<string> = new Set(['sketch', 'plane', 'axis'])

/**
 * `_vis_targets`: the selection (in the 3D view and the lists) that is still showing or, when none of it is, the thing under
 * the pointer (the Aim). While a feature dialog is open only the thing under the pointer counts.
 */
export function visibilityTargets(c: Pick<VisibilityContext, 'tabKind' | 'dialogOpen' | 'selection' | 'studio' | 'assembly' | 'selectedFeature'>): VisibilityTargets {
  const out: VisibilityTargets = { parts: [], features: [] }
  const add = (list: string[], id: string): void => {
    if (!list.includes(id)) list.push(id)
  }
  if (c.tabKind === 'assembly') {
    const hidden = new Set(c.assembly?.instances.filter((i) => i.hidden).map((i) => i.id) ?? [])
    const known = new Set(c.assembly?.instances.map((i) => i.id) ?? [])
    const take = (item: SelectionItem | null, skipHidden: boolean): void => {
      if (item?.instance !== undefined && known.has(item.instance) && !(skipHidden && hidden.has(item.instance))) add(out.parts, item.instance)
    }
    if (!c.dialogOpen) for (const i of c.selection.getState().items) take(i, true)
    if (out.parts.length === 0) take(c.selection.getAimed(), false)
    return out
  }
  const o = c.studio
  if (o === null) return out
  const parts = new Map(o.parts.map((p) => [p.id, p]))
  const eyes = new Map(o.features.map((f) => [f.id, f]))
  const take = (item: SelectionItem | null, skipHidden: boolean): void => {
    if (item === null) return
    const feature = (id: string | null): void => {
      // the three default planes have no row (and no eye) in the client: P hides them
      if (id !== null && eyes.has(id) && !(skipHidden && eyes.get(id)!.hidden)) add(out.features, id)
    }
    if (item.kind === 'face' || item.kind === 'edge' || item.kind === 'vertex' || item.kind === 'part') {
      const p = item.part === null ? undefined : parts.get(item.part)
      if (p !== undefined && !(skipHidden && p.hidden)) add(out.parts, p.id)
    } else if (item.kind === 'plane') feature(item.key !== null && item.key in o.planes ? item.key : null)
    else if (item.kind === 'axis') feature(item.key !== null && item.key in o.axes ? item.key : null)
    else if (item.kind === 'region') feature(item.part)
  }
  if (!c.dialogOpen) {
    for (const i of c.selection.getState().items) take(i, true)
    const f = c.selectedFeature === null ? undefined : eyes.get(c.selectedFeature)
    if (f !== undefined && FEATURE_EYES.has(f.type) && !f.hidden) add(out.features, f.id)
  }
  if (out.parts.length === 0 && out.features.length === 0) take(c.selection.getAimed(), false)
  return out
}

/** Edits go out one at a time, each after the last was answered; the first refusal stops the rest. */
let chain: Promise<void> = Promise.resolve()

function sendAll(c: VisibilityContext, title: string, edits: readonly Record<string, unknown>[]): void {
  if (edits.length === 0) return
  chain = chain.then(async () => {
    try {
      for (const e of edits) await c.send(e)
    } catch (e) {
      c.fail(title, e)
    }
  })
}

/**
 * The commands by name (the names input/bindings.ts and shell/menus.ts use). Each returns false when it does not act here,
 * leaving the key to the browser: with no 3D view of a Part Studio or an Assembly, or with a sketch open (`_vis_keys_apply`).
 */
export function visibilityCommands(c: VisibilityContext): Record<string, () => boolean> {
  const studio = c.tabKind === 'studio'
  const assembly = c.tabKind === 'assembly'
  const applies = (studio || assembly) && !c.sketchOpen
  const act = (run: () => void): (() => boolean) => () => {
    if (!applies) return false
    run()
    return true
  }
  /** The edits are the Document's: a read-only Document takes none. */
  const edit = (run: () => void): (() => boolean) =>
    act(() => {
      if (!c.readOnly) run()
    })

  return {
    // P (card I9.8, the owner's rule rather than the desktop's switch): the planes' own state, the default ones' and the ones
    // a person made (their eye). Any plane showing: hide them all; all hidden: show them all. An eye brings one back alone.
    'view.planes': act(() => {
      if (studio) togglePlanes(c, false)
    }),
    // Shift+P: planes, construction axes and mate connectors, by the same rule: any showing, hide all; none, show all
    'view.construction': act(() => {
      if (studio) togglePlanes(c, true)
      else c.view.setConnectorsAndMates(!(c.view.getState().connectors || c.view.getState().mates))
    }),
    'view.connectors': act(() => void c.view.toggleConnectors()),
    'view.mates': act(() => void c.view.toggleMates()),

    // Shift+H: every sketch hidden when any shows, else every sketch shown (a view setting kept on the server)
    'view.sketches': edit(() => {
      const o = c.studio
      if (!studio || o === null || o.sketches.length === 0) return
      const eyes = new Map(o.features.map((f) => [f.id, f.hidden]))
      const ids = o.sketches.map((s) => s.id)
      const hide = ids.some((id) => eyes.get(id) !== true)
      sendAll(c, hide ? 'Could not hide the sketches' : 'Could not show the sketches', [{ op: 'set_features_hidden', ids, value: hide }])
    }),

    // Y: the selected parts, sketches, planes and axes, else what is under the pointer
    'view.hide': edit(() => {
      const t = visibilityTargets(c)
      if (assembly) sendAll(c, 'Could not hide the instances', t.parts.map((instance) => ({ op: 'set_hidden', instance, value: true })))
      else {
        const edits: Record<string, unknown>[] = t.parts.map((part) => ({ op: 'set_part_override', part, hidden: true }))
        if (t.features.length > 0) edits.push({ op: 'set_features_hidden', ids: t.features, value: true })
        sendAll(c, 'Could not hide the parts', edits)
      }
    }),

    // Shift+Y (`_show_all_parts`, `show_all`): every hidden part (a hidden Context too), and isolation ends
    'view.showAll': act(() => {
      c.view.endIsolation()
      if (c.readOnly) return
      if (assembly) {
        const hidden = c.assembly?.instances.filter((i) => i.hidden) ?? []
        sendAll(c, 'Could not show the instances', hidden.map((i) => ({ op: 'set_hidden', instance: i.id, value: false })))
        return
      }
      const o = c.studio
      if (o === null) return
      const edits: Record<string, unknown>[] = o.parts.filter((p) => p.hidden).map((p) => ({ op: 'set_part_override', part: p.id, hidden: false }))
      const contexts = o.features.filter((f) => f.type === 'context' && f.hidden).map((f) => f.id)
      if (contexts.length > 0) edits.push({ op: 'set_features_hidden', ids: contexts, value: false })
      sendAll(c, 'Could not show the parts', edits)
    }),

    // Shift+I: only the selected (or aimed-at) parts are drawn; again, all are. A view setting of the client
    'view.isolate': act(() => void c.view.toggleIsolate(visibilityTargets(c).parts)),
    // Shift+T: the selected (or aimed-at) parts see-through; again, solid; with none, every part solid
    'view.transparent': act(() => void c.view.toggleTransparent(visibilityTargets(c).parts)),
  }
}

/** The ids of a Part Studio's own planes (and axes) and whether each shows, by their eyes. */
function eyed(o: StudioOutputs | null, types: readonly string[]): { id: string; hidden: boolean }[] {
  return o === null ? [] : o.features.filter((f) => types.includes(f.type)).map((f) => ({ id: f.id, hidden: f.hidden }))
}

/** Whether any plane shows: the default planes, or one a person made whose eye is open. */
export function anyPlaneShown(view: ViewState, o: StudioOutputs | null): boolean {
  return view.hiddenPlanes.length < DEFAULT_PLANES.length || eyed(o, ['plane']).some((f) => !f.hidden)
}

/**
 * P, and Shift+P with `all`: when anything of the set shows, hide all of it; else show all of it. The default planes (and the
 * mate connectors) are the tab's client view state; the planes and axes a person made go to the server as set_features_hidden,
 * the edit the eye on their row sends, so the eye and the key always agree. A read-only Document decides on the default planes alone.
 */
function togglePlanes(c: VisibilityContext, all: boolean): void {
  const types = all ? ['plane', 'axis'] : ['plane']
  const mine = c.readOnly ? [] : eyed(c.studio, types)
  const s = c.view.getState()
  const showing = s.hiddenPlanes.length < DEFAULT_PLANES.length || mine.some((f) => !f.hidden) || (all && s.connectors)
  c.view.setHiddenPlanes(showing ? DEFAULT_PLANES.map((p) => p.name) : [])
  if (all) c.view.setConnectors(!showing)
  const ids = mine.filter((f) => f.hidden === !showing).map((f) => f.id)
  // hiding: the shown ones; showing: the hidden ones
  if (ids.length > 0) sendAll(c, showing ? 'Could not hide the planes' : 'Could not show the planes', [{ op: 'set_features_hidden', ids, value: showing }])
}

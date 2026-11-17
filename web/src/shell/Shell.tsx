import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import type { ConnectionStore } from '../net/connection'
import { addTab, ApiError, closeDocument, deleteTab, fetchDocumentTabs, renameTab, saveDocument, takeOver } from '../net/api'
import type { TabInfo } from '../net/connection'
import { useConnection } from '../net/react'
import { PadDemo, padResult } from '../pad/PadDemo'
import type { PadDemoOptions, PadResult } from '../pad/PadDemo'
import { FeatureDialog } from '../dialog/FeatureDialog'
import { ExportDialog } from '../files/ExportDialog'
import { SavedNote } from '../files/SavedNote'
import type { ExportFormat, ExportResult } from '../files/filesApi'
import type { PreviewState } from '../dialog/useFeatureDialog'
import { AssemblyPanel } from '../assembly/AssemblyPanel'
import { AssemblyViewport } from '../assembly/AssemblyViewport'
import { instanceItem, selectedInstance } from '../assembly/select'
import { useAssembly } from '../assembly/useAssembly'
import { assemblyCommands, assemblyStrip, assemblyToolState, instancesOf, isAuthoringTool, S10_NOTE, useAssemblyTypes } from '../assembly/strip'
import { FeaturePanel } from '../features/FeaturePanel'
import { useFeatureEdits } from '../features/useFeatureEdits'
import { useStudioOutputs } from '../features/studioOutputs'
import { SelectionChip } from '../selection/SelectionChip'
import { filterKinds, FILTER_KEYS } from '../selection/filter'
import { partItem, SelectionStore } from '../selection/store'
import { useSelection } from '../selection/useSelection'
import { ASSEMBLY_TOOLBAR, DESKTOP_TOOLBAR, FEATURE_STRIP } from '../tools/defs'
import { picksOf } from '../tools/picks'
import type { Pick } from '../tools/picks'
import { DesktopToolbar } from '../tools/DesktopToolbar'
import { ToolStrip } from '../tools/ToolStrip'
import { stateOf, useToolTypes } from '../tools/useToolTypes'
import { SketchBar } from '../sketch/SketchBar'
import { SketchChip } from '../sketch/SketchChip'
import { DimensionDock, DimensionHint } from '../sketch/DimensionPad'
import { DimensionTool } from '../sketch/dimension'
import { CopyHint, EditDock, EditHint } from '../sketch/EditPad'
import { CopyTools } from '../sketch/tools/copy'
import { CopyDock } from '../sketch/CopyPanel'
import { EditTools } from '../sketch/tools/edit'
import { constructionPressed, SketchStore } from '../sketch/store'
import { ConstraintHint, ConstraintNote } from '../sketch/ConstraintNote'
import { attachKeys, keymapFor } from '../input/keys'
import type { KeyCommand } from '../input/keys'
import { sketchKeys } from '../sketch/keys'
import { featureDialogKeys } from '../dialog/FeatureDialog'
import { desktopCommands } from './desktopKeys'
import { ConstraintStore, isConstrainTool } from '../sketch/constraints'
import type { SketchTarget } from '../sketch/store'
import { DiscardSketch, PlaneChooser } from '../sketch/StartDialogs'
import { startSketch } from '../sketch/start'
import { DESKTOP_SKETCH_TOOLBAR, runSketchTool, SKETCH_STRIP, sketchRowCommands, sketchToolState, stripActive } from '../sketch/strip'
import { StudioViewport } from '../viewport/StudioViewport'
import type { PreviewLook } from '../viewport/controller'
import type { StudioOutputs } from '../wire'
import { DialogArea, DialogHost } from './DialogArea'
import { explainError } from './errors'
import { DebugPopover, useDebugPanel } from './LinkStatus'
import { ConfirmDialog } from './Modal'
import { ReadOnlyBanner } from './ReadOnlyBanner'
import { holderPlace } from './holder'
import { TextPrompt } from './TextPrompt'
import { ShellContext } from './ShellContext'
import type { ShellApi } from './ShellContext'
import { TabBar } from './TabBar'
import { TopBar } from './TopBar'
import { ModeProvider, useMode } from './mode'
import { clampLeftWidth, columnWidth, desktopColumnWidth, readLeftWidth, storeLeftWidth } from './layout'
import { Splitter } from './Splitter'
import { isRunnable, MENU_BAR, STANDARD_VIEW_ITEMS } from './menus'
import type { Commands, MenuItem, MenuState } from './menus'
import { runViewCommand, viewOnScreen } from '../viewport/viewCommands'
import { ViewVisibility } from '../viewport/visibility'
import { anyPlaneShown, visibilityCommands } from './visibilityCommands'
import { tokenVars } from './vars'
import './shell.css'

/** Below this window width the desktop's Home and Search tools show their icons only. */
const NARROW_BELOW = 1180

function useWindowWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const on = () => setWidth(window.innerWidth)
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return width
}

interface DialogState {
  title: string
  content: ReactNode
  actions?: ReactNode
}

/** The small dialog over the page, if one is open. */
type Modal =
  | { kind: 'rename'; tab: TabInfo }
  | { kind: 'delete'; tab: TabInfo }
  | { kind: 'leave' }
  | { kind: 'takeover' }
  | { kind: 'plane' }
  | { kind: 'discard' }
  | { kind: 'message'; title: string; text: string }
  | { kind: 'confirm'; title: string; text: string; label: string; run: () => void }

declare global {
  interface Window {
    /** Hands tests (and the screenshot script) the shell's API; nothing in the client reads it. */
    __freeshape?: {
      shell: ShellApi
      /** Test hook: open the dialog area with one number field and the pad docked under it (see src/pad/PadDemo.tsx). */
      openPadDemo: (opts?: PadDemoOptions) => void
      /** Test hook: how the last demo pad ended, or null. */
      padResult: () => PadResult | null
      /**
       * Test hook: open a sketch session on the tab on screen (I7.7; the way into a sketch from the screen is card I7.13): an
       * existing sketch feature `{ feature }`, or a new sketch on `{ plane }` (a plane reference as a sketch's plane param stores it).
       */
      openSketch: (target: { feature: string } | { plane: Record<string, unknown> }) => void
    }
  }
}

/**
 * The app shell, built to web/design/layout.html: top bar, tool strip, and under them the feature panel column
 * (with the dialog area over it) beside the viewport. Regions carry the layout's `data-region` names.
 * The connection state in the top bar opens the debug panel (round trips) over the viewport.
 */
export function Shell(props: ShellProps) {
  return (
    <ModeProvider>
      <ShellBody {...props} />
    </ModeProvider>
  )
}

interface ShellProps {
  store: ConnectionStore
  documentId: string
  version?: string
  onLeave: () => void
}

function ShellBody({ store, documentId, version, onLeave }: ShellProps) {
  const mode = useMode()
  const { tabs, document: doc, readOnly, holder, client } = useConnection(store)
  const [selected, setSelected] = useState<string | null>(null)
  // One selection for the view, the chip and the Parts list; a part is one kind of item in it (see ShellApi.selection)
  const [selection] = useState(() => new SelectionStore())
  const picked = useSelection(selection)
  const [pickedFeature, setPickedFeature] = useState<{ tab: string; id: string; revision: number } | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  // Desktop mode: the width the left column was dragged to (kept per device), and the viewport a dialog floats over
  const [leftWidth, setLeftWidth] = useState(readLeftWidth)
  const [viewportEl, setViewportEl] = useState<HTMLElement | null>(null)
  const [dialog, setDialog] = useState<DialogState | null>(null)
  // the Export dialog (card I11.9): over the panel like a feature dialog; `format` is the one a menu item names
  const [exporting, setExporting] = useState<{ tab: string; format?: ExportFormat } | null>(null)
  // where a saved export went (I11.14): a note until it is dismissed or the next export
  const [savedNote, setSavedNote] = useState<string | null>(null)
  const dismissSaved = useCallback(() => setSavedNote(null), [])
  // the feature dialog open over the panel (the dialog area is its own: it carries Cancel and Accept), and the preview it drew
  const [editing, setEditing] = useState<{ tab: string; id: string; name: string; create?: { type: string; picks: Pick[] } } | null>(null)
  const [preview, setPreview] = useState<(PreviewState & { feature: string }) | null>(null)
  // the model the open dialog picks against (card F3): drawn while the dialog is open, with its preview as a ghost over it
  const [dialogBase, setDialogBase] = useState<StudioOutputs | null>(null)
  // after Accept the preview stays drawn until the revision it made arrives, so the view does not flash the old model
  const [holdUntil, setHoldUntil] = useState<number | null>(null)
  const debug = useDebugPanel()
  // the sketch this client has open (one at a time, kept on the server); the sketch strip and view show it on its tab
  const [sketchStore] = useState(() => new SketchStore(store))
  useEffect(() => {
    sketchStore.attach()
    return () => sketchStore.detach()
  }, [sketchStore])
  const sketch = useSyncExternalStore(sketchStore.subscribe, sketchStore.getState)
  // the Constrain group: which tools fit the selection (the server says), and Delete on a selected constraint
  const [constraints] = useState(() => new ConstraintStore(sketchStore, store))
  // the keyboard's commands now: Desktop mode's are built on every render (desktopKeys.ts); the keymap is the mode's
  const keys = useRef<{ mode: string; desktop: Record<string, KeyCommand> }>({ mode, desktop: {} })
  useEffect(() => {
    constraints.attach()
    // the keyboard: one listener, one keymap (input/keys.ts). Tablet: Esc leaves constraint pick mode first, then steps back in
    // the sketch, Delete removes a selected constraint. Desktop: the desktop's keys (input/bindings.ts)
    const tablet = sketchKeys(sketchStore, constraints)
    const off = attachKeys(
      () => (keys.current.mode === 'desktop' ? keys.current.desktop : tablet),
      () => keymapFor(keys.current.mode === 'desktop' ? 'desktop' : 'tablet'),
    )
    return () => {
      off()
      constraints.detach()
    }
  }, [constraints, sketchStore])
  const constraintState = useSyncExternalStore(constraints.subscribe, constraints.getState)
  // the Dimension tool of that sketch (I7.11): its pad opens in the dialog area, through refs to what is built further down
  const [dock] = useState(() => new DimensionDock())
  const [dimension] = useState(() => new DimensionTool({ sketch: sketchStore, transport: store, openPad: dock.open, closePad: dock.close }))
  useEffect(() => {
    dimension.attach()
    return () => dimension.detach()
  }, [dimension])
  // the Dimension tool is on while it is the sketch's tool (the strip, the quick menu and a tool tapped again all go through the store)
  useEffect(() => {
    dimension.setActive(sketch.tool === 'dimension')
  }, [dimension, sketch.tool, sketch.toolSeq])
  // Trim, Corner fillet and Use edge (I7.12): the radius pad opens in the dialog area like the Dimension pad
  const [editDock] = useState(() => new EditDock())
  const [edit] = useState(() => new EditTools({ sketch: sketchStore, transport: store, openPad: editDock.open, closePad: editDock.close }))
  useEffect(() => {
    edit.attach()
    return () => edit.detach()
  }, [edit])
  // Mirror (I14.2), and the patterns after it: the copy tools collect picks, preview and send in one place
  const [copyDock] = useState(() => new CopyDock())
  const [copy] = useState(() => new CopyTools({ sketch: sketchStore, transport: store, openPanel: copyDock.open, closePanel: copyDock.close, variables: copyDock.variables }))
  useEffect(() => {
    copy.attach()
    return () => copy.detach()
  }, [copy])
  const dimensionOn = useSyncExternalStore(dimension.subscribe, dimension.getState).active
  const [modal, setModal] = useState<Modal | null>(null)
  const [saving, setSaving] = useState(false)
  const width = useWindowWidth()

  const currentTab = tabs.some((t) => t.id === selected) ? selected : (tabs[0]?.id ?? null)
  const currentTabRef = useRef(currentTab)
  const docId = useRef(doc?.id ?? null)
  useEffect(() => {
    currentTabRef.current = currentTab
    docId.current = doc?.id ?? null
  })
  // a sketch is open here when it is open at all and belongs to the tab on screen
  const sketchHere = sketch.open && sketch.view !== null && sketch.view.tab === currentTab
  const desktop = mode === 'desktop'
  const column = desktop ? desktopColumnWidth(width, collapsed, leftWidth) : columnWidth(width, collapsed)
  const tabKind = tabs.find((t) => t.id === currentTab)?.kind ?? null
  const outputs = useStudioOutputs(store, currentTab)
  // the outputs of the tab on screen (the loader keeps the last Part Studio's while an Assembly is open)
  const studio = outputs !== null && outputs.tab === currentTab && tabKind === 'studio' ? outputs : null
  // a feature stays selected until the selection of the view changes (the stamp), and the other way round: picking clears it
  const selectedFeature =
    studio !== null && pickedFeature?.tab === currentTab && pickedFeature.revision === picked.revision && studio.features.some((f) => f.id === pickedFeature.id)
      ? pickedFeature.id
      : null

  // the Parts list shows a part as selected when it is the one thing selected
  const only = picked.items.length === 1 ? picked.items[0]! : null
  const selectedPart = studio !== null && only?.kind === 'part' && studio.parts.some((p) => p.id === only.part) ? only.part : null

  // what is selected belongs to the tab on screen: a tab change empties it
  useEffect(() => {
    selection.clear()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEditing(null)
    setExporting(null)
  }, [currentTab, selection])

  const api = useMemo<ShellApi>(
    () => ({
      openDialog: (title, content, actions) => {
        if (editing !== null) return
        // Tablet mode: the dialog area is the column's own box, so the column opens for it. Desktop mode: it floats over
        // the viewport, so a column collapsed in a sketch stays collapsed under the Dimension box.
        if (mode !== 'desktop') setCollapsed(false)
        setDialog({ title, content, actions })
      },
      closeDialog: () => setDialog(null),
      dialogOpen: dialog !== null || editing !== null || exporting !== null,
      panelCollapsed: collapsed,
      setPanelCollapsed: setCollapsed,
      selectedFeature,
      selectFeature: (id) => {
        if (id !== null) selection.clear()
        setPickedFeature(id === null || currentTab === null ? null : { tab: currentTab, id, revision: selection.getState().revision })
      },
      selectedPart,
      selectPart: (id) => {
        if (id !== null) selection.set([partItem(id)])
        else if (selection.getState().items.every((i) => i.kind === 'part')) selection.clear()
      },
      selection,
    }),
    [mode, dialog, editing, exporting, collapsed, selectedFeature, selectedPart, currentTab, selection],
  )
  // the dialog area closed under the Dimension pad (its close button): the dimension is given up
  useEffect(() => {
    if (!api.dialogOpen) {
      dimension.padGone()
      edit.padGone()
      copy.panelGone()
    }
  }, [api, dimension, edit, copy])
  useEffect(() => {
    window.__freeshape = {
      shell: api,
      openPadDemo: (o = {}) =>
        api.openDialog(o.title ?? 'Extrude 1', <PadDemo label={o.label ?? 'Depth'} value={o.value ?? '25 mm'} variables={o.variables ?? ['depth', 'width', 'wall']} />),
      padResult,
      openSketch: (target) => {
        const tab = currentTabRef.current
        if (tab !== null && docId.current !== null) sketchStore.open({ document: docId.current, tab, ...target } as SketchTarget)
      },
    }
    return () => {
      delete window.__freeshape
    }
  }, [api, sketchStore])

  const name = doc?.name ?? 'This Document'
  const dirty = !readOnly && tabs.some((t) => t.dirty)
  const closeModal = useCallback(() => setModal(null), [])
  const fail = (title: string, e: unknown) => setModal({ kind: 'message', title, text: explainError(e, name) })
  const failText = useCallback((title: string, text: string) => setModal({ kind: 'message', title, text }), [])
  const edits = useFeatureEdits({ store, tab: tabKind === 'studio' ? currentTab : null, readOnly, documentName: name, onError: failText })

  const editingHere = editing !== null && editing.tab === currentTab ? editing : null
  const panelEdits = useMemo(() => (editingHere ? { ...edits, disabled: true } : edits), [edits, editingHere])
  const startEdit = useCallback(
    (feature: { id: string; name: string }) => {
      if (currentTab === null || dialog !== null) return
      // a sketch is edited in the sketch session, not in a feature dialog
      if (studio?.features.find((f) => f.id === feature.id)?.type === 'sketch') {
        if (docId.current !== null) sketchStore.open({ document: docId.current, tab: currentTab, feature: feature.id })
        return
      }
      setCollapsed(false)
      setExporting(null)
      setPreview(null)
      setDialogBase(null)
      setHoldUntil(null)
      setEditing({ tab: currentTab, id: feature.id, name: feature.name })
    },
    [currentTab, dialog, studio, sketchStore],
  )
  // a mouse double click (`_viewport_double`): a sketch's region opens the sketch, a plane of the studio opens its dialog; nothing else, and nothing while a dialog or a sketch has the view
  useEffect(() => {
    selection.setDoubleClickHandler((item) => {
      if (tabKind !== 'studio' || studio === null || editingHere !== null || dialog !== null || sketch.open || sketch.opening) return
      const id = item.kind === 'region' ? item.part : item.kind === 'plane' ? item.key : null
      const feature = id === null ? undefined : studio.features.find((f) => f.id === id && (item.kind === 'region' ? f.type === 'sketch' : f.type === 'plane'))
      if (feature !== undefined) startEdit(feature)
    })
    return () => selection.setDoubleClickHandler(null)
  })
  const endEdit = useCallback((r: { accepted: boolean; revision: number | null }) => {
    setEditing(null)
    setDialogBase(null)
    if (r.accepted && r.revision !== null) setHoldUntil(r.revision)
    else setPreview(null)
  }, [])
  // what each tool can do with the selection, from the server; a tool tap opens the create dialog with the selection as picks
  const picks = useMemo(() => picksOf(picked.items), [picked.items])
  const toolTypes = useToolTypes(doc?.id ?? null, tabKind === 'studio' ? currentTab : null, studio?.revision ?? 0, picks, editing !== null || dialog !== null)
  const startTool = useCallback(
    (type: string, withPicks?: Pick[]) => {
      if (currentTab === null || tabKind !== 'studio' || editing !== null || dialog !== null) return
      setCollapsed(false)
      setExporting(null)
      setPreview(null)
      setHoldUntil(null)
      setEditing({ tab: currentTab, id: '', name: toolTypes?.get(type)?.label ?? type, create: { type, picks: withPicks ?? picks } })
      // the tool took the selection; the dialog's active field takes the next picks
      selection.clear()
    },
    [currentTab, tabKind, editing, dialog, toolTypes, picks, selection],
  )
  // the Sketch tool: a new sketch on the selected plane or face, else ask for a plane
  const beginSketch = useCallback(
    (plane?: string) => {
      if (currentTab === null || docId.current === null || tabKind !== 'studio' || readOnly) return
      const target = { document: docId.current, tab: currentTab }
      if (plane !== undefined) return void sketchStore.open({ ...target, plane: { kind: 'std', name: plane } })
      startSketch({ ...target, store: sketchStore, items: picked.items }).then(
        (r) => (r === 'choose' ? setModal({ kind: 'plane' }) : r === null ? selection.clear() : failText('Could not start the sketch', r)),
        (e) => fail('Could not start the sketch', e),
      )
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentTab, tabKind, readOnly, sketchStore, picked.items, selection, failText],
  )
  // a sketch.open the server refuses leaves no sketch open, so the sketch chip that shows refusals is not there: say it here (F12)
  useEffect(
    () =>
      sketchStore.subscribe(() => {
        const s = sketchStore.getState()
        if (s.open || s.opening || s.error === null) return
        failText('Could not start the sketch', s.error)
        sketchStore.clearError()
      }),
    [sketchStore, failText],
  )
  // Shift+E / Shift+W in a sketch (the desktop's _feature_from_sketch): the sketch is finished, and once the revision it made
  // has arrived the feature opens with the sketch's regions picked. A new sketch is the one the Part Studio did not have before.
  const afterSketch = useRef<{ type: string; feature: string | null; known: ReadonlySet<string> } | null>(null)
  useEffect(() => {
    const p = afterSketch.current
    if (p === null || sketch.open || sketch.committed === null || studio === null || studio.revision < sketch.committed) return
    afterSketch.current = null
    const made = studio.sketches.find((k) => (p.feature !== null ? k.id === p.feature : !p.known.has(k.id)))
    startTool(p.type, made ? made.regions.map((_, i): Pick => ({ part: made.id, kind: 'region', index: i + 1 })) : undefined)
  }, [sketch.open, sketch.committed, studio, startTool])
  const showPreview = preview !== null && (editingHere !== null || (holdUntil !== null && (studio?.revision ?? 0) < holdUntil))
  // while a dialog is open the view draws its base (what picks resolve against) and the preview as a ghost over it;
  // until the base has arrived, and after Accept, the preview itself is drawn as before
  const base = editingHere !== null && dialogBase !== null && dialogBase.tab === editingHere.tab ? dialogBase : null
  const drawn = base ?? (showPreview ? preview.outputs : studio)
  const look: PreviewLook | null = showPreview ? { number: preview.number, feature: preview.feature, ghost: base === null ? null : preview.outputs } : null
  const variables = useMemo(() => Object.keys(studio?.variables ?? {}).map((n) => n.replace(/^#/, '')), [studio])
  useEffect(() => dock.bind(api, variables))
  useEffect(() => editDock.bind(api, variables))
  useEffect(() => copyDock.bind(api, variables))
  const assembly = useAssembly({ store, tab: currentTab, tabKind, readOnly, documentName: name, onError: failText })
  // the visibility keys (card I9.8): the View settings kept per tab here, the parts' and sketches' eyes kept on the server
  const [visibility] = useState(() => new ViewVisibility())
  const tabView = visibility.tab(`${doc?.id ?? ''}:${currentTab ?? ''}`)
  const viewState = useSyncExternalStore(tabView.subscribe, tabView.getState)
  // Desktop mode's Select menu (I9.14): this tab's filter goes to every pick and to the box; Tablet mode has none
  useEffect(() => {
    selection.setUserFilter(desktop ? filterKinds(viewState.select) : undefined)
  }, [selection, desktop, viewState.select])
  const assemblyTypes = useAssemblyTypes(documentId, tabKind === 'assembly' ? currentTab : null)

  const add = async (kind: 'studio' | 'assembly') => {
    try {
      const r = await addTab(documentId, kind)
      store.setTabs(r.tabs)
      setSelected(r.tab.id)
    } catch (e) {
      fail('Could not add the tab', e)
    }
  }
  const rename = async (tab: TabInfo, newName: string): Promise<string | void> => {
    try {
      store.setTabs((await renameTab(documentId, tab.id, newName)).tabs)
      setModal(null)
    } catch (e) {
      return explainError(e, name)
    }
  }
  const remove = async (tab: TabInfo) => {
    setModal(null)
    try {
      store.setTabs((await deleteTab(documentId, tab.id)).tabs)
    } catch (e) {
      fail(`Could not delete "${tab.name}"`, e)
    }
  }
  /** Save, then take in the new dirty marks at once (the socket tells everyone else). */
  const save = async (): Promise<boolean> => {
    setSaving(true)
    try {
      await saveDocument(documentId, client)
      store.setTabs((await fetchDocumentTabs(documentId)).tabs)
      return true
    } catch (e) {
      fail('Could not save', e)
      return false
    } finally {
      setSaving(false)
    }
  }
  /** Close the Document on the server and go home. A server over one Document keeps it open (409): leave anyway. */
  const leave = async (discard = false) => {
    setModal(null)
    try {
      await closeDocument(documentId, discard)
    } catch (e) {
      if (!(e instanceof ApiError && (e.status === 409 || e.status === 404))) {
        fail('Could not close the Document', e)
        return
      }
    }
    onLeave()
  }
  const back = () => (dirty ? setModal({ kind: 'leave' }) : void leave())
  const doTakeOver = async () => {
    setModal(null)
    try {
      await takeOver(documentId)
      store.watch(documentId, await fetchDocumentTabs(documentId))
      store.reload()
    } catch (e) {
      fail('Could not take over', e)
    }
  }

  const resizeLeft = useCallback((w: number) => {
    const next = clampLeftWidth(w, window.innerWidth)
    setLeftWidth(next)
    storeLeftWidth(next)
  }, [])
  // what the Desktop menus can run now; an item with no command here is shown disabled ("Desktop only for now")
  const currentInfo = tabs.find((t) => t.id === currentTab) ?? null
  const menuState: MenuState = {
    readOnly,
    tabKind,
    sketchOpen: sketchHere,
    checks: { 'view.planes': anyPlaneShown(viewState, studio), ...Object.fromEntries(FILTER_KEYS.map((k) => [`select.${k}`, viewState.select[k]])) },
  }
  const canUndo = studio?.canUndo === true && !edits.busy && !editingHere && !sketch.open
  const canRedo = studio?.canRedo === true && !edits.busy && !editingHere && !sketch.open
  // Undo and Redo, one each for the top bar, the Desktop toolbar and Ctrl+Z / Ctrl+Y: the open sketch's, else the Part Studio's
  const undoCmd = sketchHere ? { enabled: sketch.view?.canUndo === true, run: () => sketchStore.undo() } : { enabled: canUndo, run: edits.undo }
  const redoCmd = sketchHere ? { enabled: sketch.view?.canRedo === true, run: () => sketchStore.redo() } : { enabled: canRedo, run: edits.redo }
  // Export (I11.9): the Document menu, both toolbars and the Tablet top bar open the dialog; a Part Studio's selected parts ride along
  const exportable = (tabKind === 'studio' || tabKind === 'assembly') && currentTab !== null && !sketchHere && !sketch.opening && editingHere === null
  const openExport = (format?: ExportFormat) => {
    if (!exportable || dialog !== null) return
    setCollapsed(false)
    setExporting({ tab: currentTab, format })
  }
  const selectedParts = tabKind === 'studio' && studio !== null ? [...new Set(picked.items.flatMap((i) => (i.kind === 'part' && i.part !== null ? [i.part] : [])))].flatMap((id) => studio.parts.filter((p) => p.id === id).map((p) => ({ id, name: p.name }))) : []
  const finishExport = (r: ExportResult | null) => {
    setExporting(null)
    setSavedNote(r !== null && r.root !== undefined ? `Saved ${r.name} to ${r.root}` : null)
    if (r !== null && r.skipped.length > 0) failText('Surface bodies left out', `${r.name} was saved without ${r.skipped.join(', ')}: an STL holds solid bodies only, and ${r.skipped.length === 1 ? 'this one is a surface' : 'these are surfaces'}.`)
  }
  // the Desktop feature toolbar's buttons that are not features: the ones the client has; the rest are disabled there
  const toolbarCommands = {
    undo: { run: undoCmd.run, enabled: !readOnly && undoCmd.enabled },
    redo: { run: redoCmd.run, enabled: !readOnly && redoCmd.enabled },
    fit: { run: () => void runViewCommand('fit') },
    iso: { run: () => void runViewCommand('iso') },
    export: { run: () => openExport(), enabled: exportable },
  }
  // the Desktop sketch toolbar: what a tool button does is what the tablet strip's does (a Constrain tool is tool-first)
  const askCancelSketch = () => (sketch.view?.canUndo ? setModal({ kind: 'discard' }) : sketchStore.cancel())
  const constructing = constructionPressed(sketch)
  const sketchCommands = sketchRowCommands({
    active: constraintState.picking ?? (dimensionOn ? 'dimension' : sketch.tool === 'dimension' ? 'select' : sketch.tool),
    drawing: sketch.drawing > 0,
    canUndo: undoCmd.enabled,
    canRedo: redoCmd.enabled,
    constructing: constructing,
    onTool: (id) => {
      if (isConstrainTool(id) && constraints.getState().picking === id) return constraints.leave()
      sketchStore.chooseTool(isConstrainTool(id) ? 'select' : id)
      if (isConstrainTool(id)) constraints.choose(id)
    },
    onUndo: undoCmd.run,
    onRedo: redoCmd.run,
    onDelete: () => runSketchTool(sketchStore, 'delete'),
    onConstruct: () => runSketchTool(sketchStore, 'construct'),
    onNormalTo: () => sketchStore.normalTo(),
    onFit: () => void runViewCommand('fit'),
    onFinish: () => sketchStore.commit(),
  })
  const visibilityKeys = visibilityCommands({
    tabKind,
    readOnly,
    sketchOpen: sketchHere,
    dialogOpen: editingHere !== null,
    view: tabView,
    selection,
    studio,
    assembly: assembly.outputs,
    selectedFeature,
    send: (edit) => (currentTab === null ? Promise.reject(new Error('no tab')) : store.edit(currentTab, edit)),
    fail,
  })
  const assemblyTools = assemblyCommands({
    openExport: () => openExport(),
    types: assemblyTypes,
    instances: assembly.outputs?.instances ?? [],
    selected: instancesOf(picked.items),
    readOnly,
    undo: assembly.edits.undo,
    redo: assembly.edits.redo,
    setFixedAll: assembly.edits.setFixedAll,
    view: (c) => void runViewCommand(c),
    say: failText,
  })
  const commands: Commands = {
    'view.planes': () => void visibilityKeys['view.planes']!(),
    ...Object.fromEntries(FILTER_KEYS.map((k) => [`select.${k}`, () => tabView.toggleSelect(k)])),
    'select.reset': () => tabView.resetSelect(),
    'view.showAll': () => void visibilityKeys['view.showAll']!(),
    'document.cancelSketch': askCancelSketch,
    'document.newStudio': () => void add('studio'),
    'document.newAssembly': () => void add('assembly'),
    // the client has no open and closed tabs: closing a tab takes it out of the Document, so it asks as Delete does
    'document.closeTab': () => currentInfo !== null && setModal({ kind: 'delete', tab: currentInfo }),
    'document.save': () => void save(),
    'document.open': back,
    'document.exportStep': () => openExport('step'),
    'document.exportStl': () => openExport('stl'),
    ...Object.fromEntries(STANDARD_VIEW_ITEMS.map(([v]) => [`view.${v}`, () => void runViewCommand(v)])),
    'view.fit': () => void runViewCommand('fit'),
  }

  // Desktop mode's keys, routed as the desktop routes them (desktopKeys.ts), from what is on screen now
  const runMenu = (command: string): boolean => {
    const item = MENU_BAR.flatMap((m) => m.entries).find((e): e is MenuItem => e.kind !== 'separator' && e.command === command)
    if (item === undefined || !isRunnable(item, commands, menuState)) return false
    commands[command]!()
    return true
  }
  const featureNow = selectedFeature === null ? null : (studio?.features.find((f) => f.id === selectedFeature) ?? null)
  useEffect(() => {
    keys.current = {
      mode,
      desktop: desktopCommands({
        readOnly,
        tabKind,
        sketch: sketchHere ? sketchStore : null,
        constraints,
        dialog: () => (editingHere !== null ? featureDialogKeys() : null),
        closeArea: dialog !== null ? () => setDialog(null) : exporting !== null ? () => setExporting(null) : null,
        modalOpen: modal !== null,
        selection,
        selectedFeature: featureNow && { id: featureNow.id, name: featureNow.name },
        selectedInstance: tabKind === 'assembly' ? selectedInstance(picked.items) : null,
        runMenu,
        undo: undoCmd,
        redo: redoCmd,
        assemblyUndo: assembly.edits.undo,
        assemblyRedo: assembly.edits.redo,
        beginSketch: () => beginSketch(),
        startTool: (type) => startTool(type),
        featureFromSketch: (type) => {
          afterSketch.current = { type, feature: sketch.view?.feature ?? null, known: new Set(studio?.sketches.map((k) => k.id) ?? []) }
          sketchStore.commit()
        },
        leaveSketch: () => (sketch.view?.canUndo ? setModal({ kind: 'discard' }) : sketchStore.cancel()),
        deleteFeature: (f) =>
          setModal({ kind: 'confirm', title: `Delete "${f.name}"?`, text: 'The feature is removed from the Part Studio.', label: 'Delete', run: () => void edits.deleteFeature(f.id) }),
        deleteInstance: (id) => {
          const name = assembly.outputs?.instances.find((i) => i.id === id)?.name ?? 'the instance'
          setModal({ kind: 'confirm', title: `Delete "${name}"?`, text: 'The instance and its mates are removed from the Assembly.', label: 'Delete', run: () => void assembly.edits.deleteInstance(id) })
        },
        clearFeature: () => setPickedFeature(null),
        view: viewOnScreen,
        visibility: visibilityKeys,
      }),
    }
  })

  return (
    <ShellContext.Provider value={api}>
      <DialogHost value={viewportEl}>
      <div className="shell" style={tokenVars()} data-testid="shell" data-mode={mode}>
        <TopBar
          store={store}
          currentTab={currentTab}
          readOnly={readOnly}
          saving={saving}
          canUndo={undoCmd.enabled}
          canRedo={redoCmd.enabled}
          onUndo={undoCmd.run}
          onRedo={redoCmd.run}
          debugOpen={debug.open}
          onSelectTab={setSelected}
          onAddTab={(kind) => void add(kind)}
          onRenameTab={(tab) => setModal({ kind: 'rename', tab })}
          onDeleteTab={(tab) => setModal({ kind: 'delete', tab })}
          onBack={back}
          onSave={() => void save()}
          onExport={() => openExport()}
          canExport={exportable}
          onToggleDebug={debug.toggle}
          menu={{ commands, state: menuState }}
          narrow={width < NARROW_BELOW}
        />
        {savedNote !== null && <SavedNote text={savedNote} onDismiss={dismissSaved} />}
        {readOnly && <ReadOnlyBanner name={name} holder={holder} onTakeOver={() => setModal({ kind: 'takeover' })} />}
        {sketchHere && desktop ? (
          <DesktopToolbar strip="sketch" layout={DESKTOP_SKETCH_TOOLBAR} stateOf={() => 'ok'} commands={sketchCommands} onStart={() => {}} />
        ) : sketchHere ? (
          <ToolStrip
            strip="sketch"
            defs={SKETCH_STRIP}
            stateOf={(id) => (isConstrainTool(id) ? 'ok' : sketchToolState(id))}
            marked={(id) => isConstrainTool(id) && constraints.fits(id)}
            width={width}
            toggled={(id) => (id === 'construct' ? constructing : undefined)}
            active={constraintState.picking ?? (dimensionOn ? 'dimension' : stripActive(sketch.tool === 'dimension' ? 'select' : sketch.tool))}
            onStart={(id) => {
              // Delete and Construct act on the selection and change no tool; a Constrain tool acts on it at once when it fits,
              // and otherwise waits for picks with Select on (the desktop's flow)
              if (id === 'delete' || id === 'construct') return runSketchTool(sketchStore, id)
              if (isConstrainTool(id) && constraints.getState().picking === id) return constraints.leave()
              sketchStore.chooseTool(isConstrainTool(id) ? 'select' : id)
              if (isConstrainTool(id)) constraints.choose(id)
            }}
            trailing={
              <SketchBar
                nav={sketch.nav}
                onNav={(n) => sketchStore.setNav(n)}
                onNormalTo={() => sketchStore.normalTo()}
                onCancel={() => (sketch.view?.canUndo ? setModal({ kind: 'discard' }) : sketchStore.cancel())}
                onDone={() => sketchStore.commit()}
              />
            }
          />
        ) : tabKind === 'assembly' ? (
          desktop ? (
            <DesktopToolbar layout={ASSEMBLY_TOOLBAR} strip="assembly" stateOf={() => 'off'} commands={assemblyTools} onStart={() => {}} />
          ) : (
            <ToolStrip
              strip="assembly"
              defs={assemblyStrip(assemblyTypes)}
              stateOf={(id) => assemblyToolState(id, assemblyTools)}
              note={(id) => (isAuthoringTool(id) ? S10_NOTE : undefined)}
              width={width}
              onStart={(id) => assemblyTools[id]?.run()}
            />
          )
        ) : desktop && tabKind === 'studio' ? (
          <DesktopToolbar
            layout={DESKTOP_TOOLBAR}
            stateOf={(id) => (id === 'sketch' ? 'ok' : stateOf(toolTypes, id))}
            commands={toolbarCommands}
            busy={editing !== null || dialog !== null || sketch.open || sketch.opening}
            onStart={(id) => (id === 'sketch' ? beginSketch() : startTool(id))}
          />
        ) : (
          <ToolStrip
            strip="feature"
            defs={FEATURE_STRIP}
            stateOf={(id) => (id === 'sketch' && tabKind === 'studio' ? 'ok' : stateOf(toolTypes, id))}
            disabled={tabKind !== 'studio' || editing !== null || dialog !== null || sketch.open || sketch.opening}
            width={width}
            onStart={(id) => (id === 'sketch' ? beginSketch() : startTool(id))}
          />
        )}
        <div className="shell-main">
          <div className="left-column" data-region="left-column" data-testid="left-column" style={{ width: column }}>
            {tabKind === 'assembly' ? (
              <AssemblyPanel
                collapsed={collapsed}
                locked={dialog !== null}
                outputs={assembly.outputs}
                selected={selectedInstance(picked.items)}
                edits={assembly.edits}
                onSelect={(i) => selection.set([instanceItem(i.id, i.part)])}
                onCollapsedChange={setCollapsed}
              />
            ) : (
            <FeaturePanel
              collapsed={collapsed}
              locked={dialog !== null || editingHere !== null}
              tabKind={tabKind}
              outputs={studio}
              selected={selectedFeature}
              selectedPart={selectedPart}
              edits={panelEdits}
              onEdit={studio !== null && !editingHere && dialog === null && !sketch.open ? startEdit : undefined}
              onSelect={api.selectFeature}
              onSelectPart={api.selectPart}
              onCollapsedChange={setCollapsed}
              origin={desktop ? { hidden: new Set(viewState.hiddenPlanes), toggle: tabView.toggleDefaultPlane } : undefined}
            />
            )}
            {editingHere && doc && (
              <FeatureDialog
                key={`${editingHere.tab}:${editingHere.create?.type ?? editingHere.id}`}
                store={store}
                documentId={doc.id}
                documentName={name}
                tab={editingHere.tab}
                feature={editingHere}
                create={editingHere.create}
                variables={variables}
                selection={selection}
                outputs={drawn}
                base={base}
                revision={studio?.revision ?? null}
                onPreview={(p) => setPreview(p === null ? null : { ...p, feature: editingHere.id })}
                onBase={setDialogBase}
                onDone={endEdit}
              />
            )}
            {exporting !== null && exporting.tab === currentTab && doc && (tabKind === 'studio' || tabKind === 'assembly') && (
              <ExportDialog
                key={`${exporting.tab}:${exporting.format ?? ''}`}
                documentId={doc.id}
                tab={exporting.tab}
                tabKind={tabKind}
                tabName={tabs.find((t) => t.id === exporting.tab)?.name ?? ''}
                format={exporting.format}
                selected={selectedParts}
                partCount={studio?.parts.length ?? 0}
                onDone={finishExport}
              />
            )}
            {dialog && (
              <DialogArea title={dialog.title} onClose={() => setDialog(null)} actions={dialog.actions}>
                {dialog.content}
              </DialogArea>
            )}
          </div>
          {desktop && !collapsed && <Splitter width={column} onResize={resizeLeft} />}
          {/* data-dialog-base: the view draws an open dialog's base model, the one its picks resolve against (a test hook) */}
          <div className="shell-viewport" ref={setViewportEl} data-region="viewport" data-testid="shell-viewport" data-dialog-base={base !== null ? '' : undefined}>
            {tabKind === 'assembly' ? (
              <AssemblyViewport outputs={assembly.outputs} selection={selection} drag={assembly.edits.drag} visibility={viewState} />
            ) : (
              <StudioViewport
                outputs={drawn}
                preview={look}
                selection={selection}
                tabKind={tabKind}
                sketch={sketchHere ? sketchStore : null}
                visibility={viewState}
              />
            )}
            <div className="chip-slot" data-testid="chip-slot" data-region="selection-chip">
              {sketchHere ? <SketchChip store={sketchStore} /> : <SelectionChip store={selection} />}
              {sketchHere && <ConstraintNote store={constraints} />}
              {sketchHere && <ConstraintHint store={constraints} sketch={sketchStore} />}
              {sketchHere && <DimensionHint tool={dimension} />}
              {sketchHere && <EditHint tool={edit} />}
              {sketchHere && <CopyHint tool={copy} />}
            </div>
          </div>
        </div>
        {desktop && (
          <TabBar
            tabs={tabs}
            currentTab={currentTab}
            readOnly={readOnly}
            onSelect={setSelected}
            onAdd={(kind) => void add(kind)}
            onRename={(tab) => setModal({ kind: 'rename', tab })}
            onDelete={(tab) => setModal({ kind: 'delete', tab })}
            strip={{ documentName: name, commands, state: menuState }}
          />
        )}
        {debug.open && <DebugPopover store={store} version={version} />}
        {modal?.kind === 'rename' && (
          <TextPrompt
            title="Rename tab"
            label="Name"
            initial={modal.tab.name}
            confirmLabel="Rename"
            onSubmit={(value) => rename(modal.tab, value)}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'delete' && (
          <ConfirmDialog
            title={`Delete "${modal.tab.name}"?`}
            message="The tab and everything in it is removed from the Document."
            confirm={{ label: 'Delete', danger: true }}
            onConfirm={() => void remove(modal.tab)}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'leave' && (
          <ConfirmDialog
            title={`Save changes to "${name}"?`}
            message="The Document has changes that are not saved yet. Discard throws them away, and they cannot be recovered afterwards."
            confirm={{ label: 'Save' }}
            alternative={{ label: 'Discard', danger: true, onClick: () => void leave(true) }}
            cancelLabel="Stay"
            onConfirm={() => {
              setModal(null)
              void save().then((ok) => {
                if (ok) void leave()
              })
            }}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'plane' && (
          <PlaneChooser
            onPlane={(n) => {
              setModal(null)
              beginSketch(n)
            }}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'discard' && (
          <DiscardSketch
            onDiscard={() => {
              setModal(null)
              sketchStore.cancel()
            }}
            onKeep={closeModal}
          />
        )}
        {modal?.kind === 'takeover' && (
          <ConfirmDialog
            title={`Take over "${name}"?`}
            message={`${holderPlace(holder)[0]!.toUpperCase()}${holderPlace(holder).slice(1)} will lose the right to edit it, and what it has not saved is not kept here.`}
            confirm={{ label: 'Take over', danger: true }}
            onConfirm={() => void doTakeOver()}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'confirm' && (
          <ConfirmDialog
            title={modal.title}
            message={modal.text}
            confirm={{ label: modal.label, danger: true }}
            onConfirm={() => {
              setModal(null)
              modal.run()
            }}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'message' && (
          <ConfirmDialog title={modal.title} message={modal.text} confirm={{ label: 'OK' }} cancelLabel={null} onConfirm={closeModal} onCancel={closeModal} />
        )}
      </div>
      </DialogHost>
    </ShellContext.Provider>
  )
}

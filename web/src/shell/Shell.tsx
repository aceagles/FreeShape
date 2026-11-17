import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import type { ConnectionStore } from '../net/connection'
import { addTab, ApiError, closeDocument, deleteTab, fetchDocumentTabs, renameTab, saveDocument, takeOver } from '../net/api'
import type { TabInfo } from '../net/connection'
import { useConnection } from '../net/react'
import { PadDemo, padResult } from '../pad/PadDemo'
import type { PadDemoOptions, PadResult } from '../pad/PadDemo'
import { FeatureDialog } from '../dialog/FeatureDialog'
import { useArchiveOpen, useDocumentDownload } from '../files/ArchiveFlow'
import { ExportDialog } from '../files/ExportDialog'
import { Explorer } from '../files/Explorer'
import type { ExplorerMode } from '../files/Explorer'
import { SavedNote } from '../files/SavedNote'
import { useCanvasImage } from '../files/CanvasFlow'
import { fetchCanvasImage } from '../files/canvasApi'
import { UploadProgress } from '../files/UploadProgress'
import type { UploadState } from '../files/UploadProgress'
import { fetchUploadLimit, importDevice, importFailure, insertDevice, insertedText } from '../files/filesApi'
import type { StepInsertReply } from '../files/filesApi'
import type { ExportFormat, ExportResult, ImportReply } from '../files/filesApi'
import type { PreviewState } from '../dialog/useFeatureDialog'
import { AssemblyPanel } from '../assembly/AssemblyPanel'
import { AssemblyViewport } from '../assembly/AssemblyViewport'
import { instanceItem, selectedInstance } from '../assembly/select'
import type { ViewContextEvent } from '../selection/aimCommit'
import { useAssembly } from '../assembly/useAssembly'
import { InsertPanel } from '../assembly/InsertPanel'
import { MateDialog, mateDialogKeys } from '../assembly/MateDialog'
import type { MateDone, MateStart } from '../assembly/MateDialog'
import { NEED_TWO_PARTS, preselection, previewOutputs } from '../assembly/mates'
import type { MateView } from '../assembly/mates'
import { assemblyCommands, assemblyStrip, assemblyToolState, instancesOf, useAssemblyTypes } from '../assembly/strip'
import { FeaturePanel } from '../features/FeaturePanel'
import { useFeatureEdits } from '../features/useFeatureEdits'
import { useStudioOutputs } from '../features/studioOutputs'
import { SelectionChip } from '../selection/SelectionChip'
import { filterKinds, FILTER_KEYS } from '../selection/filter'
import { itemOf, partItem, SelectionStore } from '../selection/store'
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
import { planDrop, useFileDrop } from '../viewport/fileDrop'
import type { PreviewLook } from '../viewport/controller'
import type { InstanceEntry, MateEntry, StudioOutputs } from '../wire'
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
import { Palette } from './Palette'
import { paletteEntries } from './paletteEntries'
import type { PaletteEnv } from './paletteEntries'
import { Shortcuts } from './Shortcuts'
import { ModeProvider, useMode } from './mode'
import { clampLeftWidth, columnWidth, desktopColumnWidth, readLeftWidth, storeLeftWidth } from './layout'
import { Splitter } from './Splitter'
import { isRunnable, MENU_BAR, STANDARD_VIEW_ITEMS } from './menus'
import { useContextMenu } from './Menu'
import { assemblyViewMenu, instanceMenu, mateMenu, sketchViewMenu, studioViewMenu } from './contextMenus'
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

/** Counts the mate dialogs opened, so each is a new component. */
let mateStarts = 0

/** The small dialog over the page, if one is open. */
type Modal =
  | { kind: 'rename'; tab: TabInfo }
  | { kind: 'renameInstance'; id: string; name: string }
  | { kind: 'renameMate'; id: string; name: string }
  | { kind: 'delete'; tab: TabInfo }
  | { kind: 'leave'; next?: { id: string; name: string } }
  | { kind: 'takeover' }
  | { kind: 'plane' }
  | { kind: 'shortcuts' }
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
  /** Close this Document and go home; `next` is a Document to open there, which the explorer's Documents root asks for. */
  onLeave: (next?: { id: string; name: string }) => void
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
  // the tab whose Insert panel is open (card I10.1)
  const [inserting, setInserting] = useState<string | null>(null)
  // the mate dialog (card I10.6): what opened it, the preview it draws, and the last preview kept until an accepted mate's revision arrives
  const [mateStart, setMateStart] = useState<MateStart | null>(null)
  const [mateView, setMateView] = useState<MateView | null>(null)
  const [mateHold, setMateHold] = useState<{ view: MateView; until: number } | null>(null)
  // the file explorer (I11.7): the Component library and the two imports; the mode says which button of an entry leads
  const [exploring, setExploring] = useState<ExplorerMode | null>(null)
  // what the last STEP inserted from the explorer said, for the Insert panel that shows again when the explorer closes (I11.11)
  const [insertNote, setInsertNote] = useState<string | null>(null)
  // where a saved export went (I11.14): a note until it is dismissed or the next export
  const [savedNote, setSavedNote] = useState<string | null>(null)
  const dismissSaved = useCallback(() => setSavedNote(null), [])
  // the feature dialog open over the panel (the dialog area is its own: it carries Cancel and Accept), and the preview it drew
  const [editing, setEditing] = useState<{ tab: string; id: string; name: string; create?: { type: string; picks: Pick[]; params?: Record<string, unknown> } } | null>(null)
  const [preview, setPreview] = useState<(PreviewState & { feature: string }) | null>(null)
  // the model the open dialog picks against (card F3): drawn while the dialog is open, with its preview as a ghost over it
  const [dialogBase, setDialogBase] = useState<StudioOutputs | null>(null)
  // after Accept the preview stays drawn until the revision it made arrives, so the view does not flash the old model
  const [holdUntil, setHoldUntil] = useState<number | null>(null)
  const debug = useDebugPanel()
  // the right-click menus of the view, the Instances and Mates lists (card I9.10); the Features, Parts and tab menus open from their own components
  const context = useContextMenu()
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
  // the Search tools palette (card I9.11): Desktop mode only; nothing in Tablet mode opens it
  const [paletteOpen, setPaletteOpen] = useState(false)
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
  // (a Selectable canvas picked in the view selects its own row, card I12.5)
  const canvasItem = picked.items.length === 1 && picked.items[0]!.kind === 'canvas' ? picked.items[0]! : null
  const pickedCanvas = studio !== null && canvasItem?.key != null && studio.features.some((f) => f.id === canvasItem.key) ? canvasItem.key : null
  const selectedFeature =
    pickedCanvas ?? (studio !== null && pickedFeature?.tab === currentTab && pickedFeature.revision === picked.revision && studio.features.some((f) => f.id === pickedFeature.id)
      ? pickedFeature.id
      : null)

  // the Parts list shows a part as selected when it is the one thing selected
  const only = picked.items.length === 1 ? picked.items[0]! : null
  const selectedPart = studio !== null && only?.kind === 'part' && studio.parts.some((p) => p.id === only.part) ? only.part : null

  // what is selected belongs to the tab on screen: a tab change empties it
  useEffect(() => {
    selection.clear()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEditing(null)
    setExporting(null)
    setInserting(null)
    setInsertNote(null)
    setMateStart(null)
    setMateHold(null)
  }, [currentTab, selection])

  const api = useMemo<ShellApi>(
    () => ({
      openDialog: (title, content, actions) => {
        if (editing !== null || mateStart !== null) return
        // Tablet mode: the dialog area is the column's own box, so the column opens for it. Desktop mode: it floats over
        // the viewport, so a column collapsed in a sketch stays collapsed under the Dimension box.
        if (mode !== 'desktop') setCollapsed(false)
        setDialog({ title, content, actions })
      },
      closeDialog: () => setDialog(null),
      dialogOpen: dialog !== null || editing !== null || exporting !== null || exploring !== null || mateStart !== null,
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
    [mode, dialog, editing, exporting, exploring, mateStart, collapsed, selectedFeature, selectedPart, currentTab, selection],
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
      setExploring(null)
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
      const id = item.kind === 'region' ? item.part : item.kind === 'plane' || item.kind === 'canvas' ? item.key : null
      const feature = id === null ? undefined : studio.features.find((f) => f.id === id && (item.kind === 'region' ? f.type === 'sketch' : f.type === item.kind))
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
    (type: string, withPicks?: Pick[], params?: Record<string, unknown>) => {
      if (currentTab === null || tabKind !== 'studio' || editing !== null || dialog !== null) return false
      setCollapsed(false)
      setExporting(null)
      setExploring(null)
      setPreview(null)
      setHoldUntil(null)
      setEditing({ tab: currentTab, id: '', name: toolTypes?.get(type)?.label ?? type, create: { type, picks: withPicks ?? picks, ...(params !== undefined ? { params } : {}) } })
      // the tool took the selection; the dialog's active field takes the next picks
      selection.clear()
      return true
    },
    [currentTab, tabKind, editing, dialog, toolTypes, picks, selection],
  )
  // the Canvas tool (card I12.5): the device's picker for a PNG or JPEG, then the generic dialog for a new canvas on the uploaded
  // image, with the selected face picked. Starting the dialog closes the explorer and the Export dialog, as every tool does.
  const startToolRef = useRef(startTool)
  useEffect(() => {
    startToolRef.current = startTool
  })
  const canvasImage = useCanvasImage(doc !== null && currentTab !== null && tabKind === 'studio' && !readOnly ? { document: doc.id, tab: currentTab, client } : null, (up) => {
    if (!startToolRef.current('canvas', undefined, { blob: up.blob, filename: up.filename })) failText('Canvas', 'Another dialog is open. Finish or cancel it, then add the image again.')
  })
  // the Sketch tool: a new sketch on the selected plane or face, else ask for a plane
  const beginSketch = useCallback(
    (plane?: string, onItems?: typeof picked.items) => {
      if (currentTab === null || docId.current === null || tabKind !== 'studio' || readOnly) return
      const target = { document: docId.current, tab: currentTab }
      if (plane !== undefined) return void sketchStore.open({ ...target, plane: { kind: 'std', name: plane } })
      startSketch({ ...target, store: sketchStore, items: onItems ?? picked.items }).then(
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
  // where this tab's canvases fetch their pictures (card I12.5); the same function while the tab is, so the textures are kept
  const canvasDoc = doc?.id ?? null
  const canvasImages = useMemo(() => (canvasDoc !== null && currentTab !== null ? (blob: string) => fetchCanvasImage(canvasDoc, currentTab, blob, client) : null), [canvasDoc, currentTab, client])
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
  // The mate dialog (card I10.6): the strip's and toolbar's mate tools, Shift+F, Shift+R and Shift+L, and Edit on a mate open it in the dialog area.
  // It takes the area from the Insert panel, the explorer and Export (they close, as every tool closes them) and refuses to start while another dialog has it.
  const mating = mateStart !== null && mateStart.tab === currentTab && tabKind === 'assembly'
  const partCount = assembly.outputs?.instances.filter((i) => i.part !== null).length ?? 0
  const mateOff = readOnly ? 'This Document is open read-only here' : assembly.outputs === null || partCount < 2 ? NEED_TWO_PARTS : dialog !== null ? 'Finish or cancel the open dialog first' : null
  const beginMate = (what: MateStart['what']) => {
    if (currentTab === null || tabKind !== 'assembly') return
    setCollapsed(false)
    setExporting(null)
    setExploring(null)
    setInserting(null)
    setInsertNote(null)
    setMateHold(null)
    setMateStart({ seq: ++mateStarts, tab: currentTab, what })
  }
  const startMate = (type: string) => {
    if (tabKind !== 'assembly' || readOnly) return
    // a mate tool pressed while the dialog is open changes its type
    if (mating) return mateDialogKeys()?.setType(type)
    if (mateOff !== null) return failText('Mate', mateOff)
    beginMate({ type, ...preselection(picked.items) })
  }
  const editMate = (id: string) => {
    if (readOnly || tabKind !== 'assembly') return
    if (mating) return failText('Mate', 'Finish or cancel the open mate first.')
    if (dialog !== null) return failText('Mate', mateOff ?? 'Finish or cancel the open dialog first')
    beginMate({ mate: id })
  }
  const endMate = (done: MateDone) => {
    setMateStart(null)
    // an accepted mate keeps drawing as previewed until the revision it made has arrived
    if (done.accepted && done.view !== null && done.revision !== null) setMateHold({ view: done.view, until: done.revision })
  }
  const held = mateHold !== null && (assembly.outputs?.revision ?? 0) < mateHold.until ? mateHold.view : null
  const drawnAssembly = useMemo(() => {
    const view = mateView ?? held
    return assembly.outputs !== null && view !== null && tabKind === 'assembly' ? previewOutputs(assembly.outputs, view) : assembly.outputs
  }, [assembly.outputs, mateView, held, tabKind])
  const mateActions = {
    edit: (m: { id: string }) => editMate(m.id),
    rename: (m: { id: string; name: string }) => setModal({ kind: 'renameMate', id: m.id, name: m.name }),
    setSuppressed: (m: { id: string }, value: boolean) => void sendEach([{ op: 'set_suppressed', mate: m.id, value }], value ? 'Could not suppress the mate' : 'Could not unsuppress the mate'),
    remove: (m: { id: string }) => void sendEach([{ op: 'delete_mates', ids: [m.id] }], 'Could not delete the mate'),
  }

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
  const leave = async (discard = false, next?: { id: string; name: string }) => {
    setModal(null)
    try {
      await closeDocument(documentId, discard)
    } catch (e) {
      if (!(e instanceof ApiError && (e.status === 409 || e.status === 404))) {
        fail('Could not close the Document', e)
        return
      }
    }
    onLeave(next)
  }
  const back = (next?: { id: string; name: string }) => (dirty ? setModal({ kind: 'leave', next }) : void leave(false, next))
  // the Document as one .fsdoc file (card I11.10): Save Document archive…, Tablet's Download Document, and Open Document archive…, which
  // leaves this Document for the new one as the file explorer's Documents root does (the unsaved-changes question included)
  const archiveDownload = useDocumentDownload(client)
  const archiveOpen = useArchiveOpen((made) => back({ id: made.id, name: made.name }), mode === 'desktop')
  const downloadDocument = () => void archiveDownload.start({ id: documentId, name }, readOnly)
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
  const exportable = (tabKind === 'studio' || tabKind === 'assembly') && currentTab !== null && !sketchHere && !sketch.opening && editingHere === null && !mating
  const openExport = (format?: ExportFormat) => {
    if (!exportable || dialog !== null) return
    setCollapsed(false)
    setExporting({ tab: currentTab, format })
  }
  // The file explorer (I11.7): the Document menu's two imports and Component library, Ctrl+Shift+L, the Desktop toolbar's Import and the Tablet strip's Import
  const explorerBusy = dialog !== null || editingHere !== null || sketchHere || sketch.opening || mating
  const openExplorer = (mode: ExplorerMode) => {
    if (explorerBusy) return
    if (!desktop) setCollapsed(false)
    setExporting(null)
    setExploring(mode)
  }
  const insertedStep = (reply: StepInsertReply, said: string) => {
    store.setTabs(reply.tabs)
    setInsertNote(said)
  }
  const importedFile = (reply: ImportReply, newTab: boolean) => {
    store.setTabs(reply.tabs)
    if (newTab) setSelected(reply.tab.id)
  }
  // A file dropped on the page (I11.8, planDrop says what each drop comes to): in Desktop mode STEP and BREP files on the view import
  // into new Part Studios one after the other, with the progress and Cancel over the view; everything else says why it did nothing.
  const [dropping, setDropping] = useState<UploadState | null>(null)
  const dropCtl = useRef<AbortController | null>(null)
  useEffect(() => () => dropCtl.current?.abort(), [])
  const importDropped = async (list: File[]) => {
    if (!doc || currentTab === null) return
    const ctl = new AbortController()
    dropCtl.current = ctl
    const asm = tabKind === 'assembly'
    let current = list[0]!
    try {
      const limit = await fetchUploadLimit()
      for (const f of list) {
        current = f
        setDropping({ name: f.name, sent: 0, total: f.size })
        const opts = { limit, signal: ctl.signal, onProgress: (sent: number, total: number) => setDropping({ name: f.name, sent, total }) }
        if (asm) {
          const a = await insertDevice(doc.id, currentTab, f, opts)
          const said = insertedText(f.name, a.instances)
          insertedStep(a, said)
          setSavedNote(said)
          continue
        }
        const r = await importDevice(doc.id, currentTab, f, true, opts)
        importedFile(r, true)
        if (r.feature?.error) failText('Import', `${f.name}: ${r.feature.error}`)
        else setSavedNote(`Imported ${f.name}`)
      }
    } catch (x) {
      if (!(x instanceof DOMException && x.name === 'AbortError')) failText('Import', `${current.name}: ${importFailure(x)}`)
    } finally {
      if (dropCtl.current === ctl) dropCtl.current = null
      setDropping(null)
    }
  }
  useFileDrop(viewportEl, (files, onView) => {
    const blocked = readOnly
      ? 'This Document is open read-only here, so nothing can be imported.'
      : currentTab === null
        ? 'This Document has no tab to import into.'
        : explorerBusy
          ? 'Close the dialog or finish the sketch first, then drop the file again.'
          : dropCtl.current !== null
            ? 'A file is still uploading. Wait for it, or cancel it.'
            : null
    const plan = planDrop(files, { desktop, onView, blocked, assembly: tabKind === 'assembly' })
    if (plan.say !== null) failText('Import', plan.say)
    if (plan.import.length > 0) void importDropped(plan.import)
  })
  const selectedParts = tabKind === 'studio' && studio !== null ? [...new Set(picked.items.flatMap((i) => (i.kind === 'part' && i.part !== null ? [i.part] : [])))].flatMap((id) => studio.parts.filter((p) => p.id === id).map((p) => ({ id, name: p.name }))) : []
  // Insert (I10.1): the Assembly toolbar's Insert, the I key and the view menu's Insert parts… open or close the panel; it floats in Desktop mode and covers the column in Tablet mode
  const insertOpen = inserting !== null && inserting === currentTab && tabKind === 'assembly'
  const toggleInsert = () => {
    if (tabKind !== 'assembly' || currentTab === null || readOnly || mating) return
    if (insertOpen) return setInserting(null)
    setCollapsed(false)
    setExporting(null)
    setInsertNote(null)
    setInserting(currentTab)
  }
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
    import: { run: () => openExplorer('new') },
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
    insertOpen,
    toggleInsert,
    types: assemblyTypes,
    instances: assembly.outputs?.instances ?? [],
    selected: instancesOf(picked.items),
    readOnly,
    undo: assembly.edits.undo,
    redo: assembly.edits.redo,
    setFixedAll: assembly.edits.setFixedAll,
    view: (c) => void runViewCommand(c),
    say: failText,
    mateType: mating ? (mateView?.type ?? ('type' in mateStart.what ? mateStart.what.type : null)) : null,
    mateOff: mating ? null : mateOff,
    startMate,
  })
  // Desktop mode's right-click menus (card I9.10): the desktop opens none while a dialog, a sketch's start or a tool has the window
  const sendEach = async (edits: readonly Record<string, unknown>[], title: string): Promise<void> => {
    try {
      for (const e of edits) await store.edit(currentTab!, e)
    } catch (e) {
      fail(title, e)
    }
  }
  const askDeleteInstance = (id: string) => {
    const label = assembly.outputs?.instances.find((i) => i.id === id)?.name ?? 'the instance'
    setModal({ kind: 'confirm', title: `Delete "${label}"?`, text: 'The instance and its mates are removed from the Assembly.', label: 'Delete', run: () => void assembly.edits.deleteInstance(id) })
  }
  const openInstanceMenu = (inst: InstanceEntry, x: number, y: number) => {
    selection.set([instanceItem(inst.id, inst.part)])
    const e = assembly.edits
    context.open({
      x,
      y,
      label: `${inst.name} menu`,
      ...instanceMenu(inst, {
        busy: e.busy || e.disabled,
        actions: {
          rename: () => setModal({ kind: 'renameInstance', id: inst.id, name: inst.name }),
          setFixed: (fixed) => e.setFixed(inst.id, fixed),
          setHidden: (hidden) => e.setHidden(inst.id, hidden),
          isolate: () => void tabView.toggleIsolate([inst.id]),
          addConnector: () => void e.addOriginConnector(inst.id),
          // the gizmo shows on the one free instance that is selected; the menu selects it and says why when it cannot
          move: () => (inst.fixed ? failText('Move', `${inst.name} is fixed. Free it first, then move it.`) : inst.hidden ? failText('Move', `${inst.name} is hidden. Show it first, then move it.`) : undefined),
          remove: () => askDeleteInstance(inst.id),
        },
      }),
    })
  }
  const onAssemblyContext = (e: ViewContextEvent) => {
    const out = assembly.outputs
    if (tabKind !== 'assembly' || out === null || dialog !== null || mating) return
    const under = e.hit !== null && (e.hit.kind === 'face' || e.hit.kind === 'edge') ? e.hit.instance : undefined
    const inst = under === undefined ? undefined : out.instances.find((i) => i.id === under)
    if (inst !== undefined) return openInstanceMenu(inst, e.x, e.y)
    context.open({
      x: e.x,
      y: e.y,
      label: 'View menu',
      ...assemblyViewMenu({
        anyHidden: out.instances.some((i) => i.hidden),
        readOnly,
        insert: toggleInsert,
        fit: () => void runViewCommand('fit'),
        view: (n) => void runViewCommand(n),
        showAll: () => void visibilityKeys['view.showAll']!(),
      }),
    })
  }
  const onStudioContext = (e: ViewContextEvent) => {
    if (tabKind !== 'studio' || studio === null) return
    if (e.sketch) {
      if (!sketchHere) return
      return context.open({ x: e.x, y: e.y, label: 'Sketch menu', ...sketchViewMenu({ normalTo: () => sketchStore.normalTo(), finish: () => sketchStore.commit(), cancel: askCancelSketch }) })
    }
    if (sketch.open || sketch.opening || editingHere !== null || dialog !== null) return
    const hit = e.hit
    const part = hit !== null && (hit.kind === 'face' || hit.kind === 'edge') ? hit.part : null
    // the parts the menu acts on: the selection when it holds the one under the pointer, else that one
    const selected = [...new Set(picked.items.flatMap((i) => (i.part !== null && (i.kind === 'face' || i.kind === 'edge' || i.kind === 'part' || i.kind === 'vertex') ? [i.part] : [])))]
    const ids = part === null ? [] : selected.includes(part) && selected.length > 1 ? selected : [part]
    const parts = ids.flatMap((id) => studio.parts.filter((p) => p.id === id).map((p) => ({ id, hidden: p.hidden })))
    const key = hit !== null && (hit.kind === 'plane' || hit.kind === 'axis') ? hit.key : null
    context.open({
      x: e.x,
      y: e.y,
      label: 'View menu',
      ...studioViewMenu({
        kind: hit?.kind ?? null,
        parts,
        anyHidden: studio.parts.some((p) => p.hidden),
        partCount: studio.parts.length,
        readOnly,
        actions: {
          newSketch: hit === null ? undefined : () => beginSketch(undefined, [itemOf(hit)]),
          normalTo: hit === null ? undefined : () => void viewOnScreen()?.normalKey?.(itemOf(hit)),
          setPartsHidden: (list, hidden) => void sendEach(list.map((p) => ({ op: 'set_part_override', part: p, hidden })), hidden ? 'Could not hide the parts' : 'Could not show the parts'),
          isolate: (list) => void tabView.toggleIsolate(list),
          selectPart: (id) => selection.set([partItem(id)]),
          deletePart: (id) => startTool('delete_part', [{ part: id, kind: 'part' }]),
          editSketch: () => {
            const f = hit === null ? undefined : studio.features.find((x) => x.id === hit.part)
            if (f !== undefined) startEdit(f)
          },
          hideSketch: () => hit !== null && hit.part !== null && edits.setFeatureHidden(hit.part, true),
          // the default planes are the tab's view state; a plane or axis a person made has an eye on the server
          hidePlane: () => {
            if (key === null) return
            if (key in studio.planes) edits.setFeatureHidden(key, true)
            else if (!viewState.hiddenPlanes.includes(key)) tabView.toggleDefaultPlane(key)
          },
          hideAxis: () => key !== null && key in studio.axes && edits.setFeatureHidden(key, true),
          showAll: () => void visibilityKeys['view.showAll']!(),
          fit: () => void runViewCommand('fit'),
          view: (n) => void runViewCommand(n),
        },
      }),
    })
  }
  const onAssemblyRowContext = (t: { kind: 'instance'; instance: InstanceEntry } | { kind: 'mate'; mate: MateEntry }, x: number, y: number) => {
    if (dialog !== null || mating) return
    if (t.kind === 'instance') return openInstanceMenu(t.instance, x, y)
    const e = assembly.edits
    context.open({ x, y, label: 'Mate menu', ...mateMenu(t.mate, assembly.outputs?.errors[t.mate.id], { busy: e.busy || e.disabled, actions: { edit: () => mateActions.edit(t.mate), rename: () => mateActions.rename(t.mate), setSuppressed: (v) => mateActions.setSuppressed(t.mate, v), remove: () => mateActions.remove(t.mate) } }) })
  }
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
    'document.saveArchive': downloadDocument,
    ...(archiveOpen.supported === true ? { 'document.openArchive': archiveOpen.pick } : {}),
    'document.shortcuts': () => setModal({ kind: 'shortcuts' }),
    'document.open': () => back(),
    'document.importNew': () => openExplorer('new'),
    'document.importInto': () => openExplorer('add'),
    'document.library': () => openExplorer('browse'),
    'document.exportStep': () => openExport('step'),
    'document.exportStl': () => openExport('stl'),
    ...Object.fromEntries(STANDARD_VIEW_ITEMS.map(([v]) => [`view.${v}`, () => void runViewCommand(v)])),
    'view.fit': () => void runViewCommand('fit'),
  }

  // what a tool button of a Part Studio does and whether it can: Sketch asks the server for nothing, Canvas needs a writable studio and a picked image first (card I12.5)
  const studioToolState = (id: string) => (id === 'sketch' ? 'ok' : id === 'canvas' ? (tabKind === 'studio' && !readOnly ? 'ok' : 'off') : stateOf(toolTypes, id))
  const runTool = (id: string) => {
    if (id === 'sketch') return beginSketch()
    if (id !== 'canvas') return startTool(id)
    if (editing === null && dialog === null && !sketch.open && !sketch.opening) canvasImage.pick()
  }
  // Desktop mode's keys, routed as the desktop routes them (desktopKeys.ts), from what is on screen now
  const runMenu = (command: string): boolean => {
    const item = MENU_BAR.flatMap((m) => m.entries).find((e): e is MenuItem => e.kind !== 'separator' && e.command === command)
    if (item === undefined || !isRunnable(item, commands, menuState)) return false
    commands[command]!()
    return true
  }
  // the Search tools palette lists the toolbar on screen (the same wiring as the row drawn below) and the menus
  const paletteEnv: PaletteEnv = sketchHere
    ? { workspace: 'sketch', layout: DESKTOP_SKETCH_TOOLBAR, stateOf: () => 'ok', commands: sketchCommands, onStart: () => {}, assemblyTypes, menuCommands: commands, menuState }
    : tabKind === 'assembly'
      ? { workspace: 'assembly', layout: ASSEMBLY_TOOLBAR, stateOf: () => 'off', commands: assemblyTools, onStart: () => {}, assemblyTypes, menuCommands: commands, menuState }
      : tabKind === 'studio'
        ? {
            workspace: 'studio',
            layout: DESKTOP_TOOLBAR,
            stateOf: studioToolState,
            commands: toolbarCommands,
            busy: editing !== null || dialog !== null || sketch.open || sketch.opening,
            onStart: runTool,
            assemblyTypes,
            menuCommands: commands,
            menuState,
          }
        : { workspace: null, layout: null, stateOf: () => 'off', commands: {}, onStart: () => {}, assemblyTypes, menuCommands: commands, menuState }
  const featureNow = selectedFeature === null ? null : (studio?.features.find((f) => f.id === selectedFeature) ?? null)
  useEffect(() => {
    keys.current = {
      mode,
      desktop: desktopCommands({
        readOnly,
        tabKind,
        sketch: sketchHere ? sketchStore : null,
        constraints,
        dialog: () => (editingHere !== null ? featureDialogKeys() : mating ? mateDialogKeys() : null),
        closeArea: dialog !== null ? () => setDialog(null) : mating ? () => setMateStart(null) : exporting !== null ? () => setExporting(null) : exploring !== null ? () => setExploring(null) : insertOpen ? () => setInserting(null) : null,
        modalOpen: modal !== null,
        selection,
        selectedFeature: featureNow && { id: featureNow.id, name: featureNow.name },
        selectedInstance: tabKind === 'assembly' ? selectedInstance(picked.items) : null,
        runMenu,
        openPalette: () => setPaletteOpen(true),
        undo: undoCmd,
        redo: redoCmd,
        assemblyUndo: assembly.edits.undo,
        assemblyRedo: assembly.edits.redo,
        toggleInsert,
        startMate,
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
          onBack={() => back()}
          onSave={() => void save()}
          onExport={() => openExport()}
          onDownload={downloadDocument}
          canExport={exportable}
          onToggleDebug={debug.toggle}
          menu={{ commands, state: menuState }}
          palette={{ open: paletteOpen, toggle: () => setPaletteOpen((o) => !o), shortcuts: () => setModal({ kind: 'shortcuts' }) }}
          narrow={width < NARROW_BELOW}
        />
        {savedNote !== null && <SavedNote text={savedNote} onDismiss={dismissSaved} />}
        {dropping !== null && (
          <div className="export-saved up-note" data-testid="drop-upload">
            <UploadProgress upload={dropping} onCancel={() => dropCtl.current?.abort()} />
          </div>
        )}
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
              note={(id) => (assemblyTools[id]?.enabled === false ? assemblyTools[id]?.note : undefined)}
              toggled={(id) => assemblyTools[id]?.pressed}
              width={width}
              onStart={(id) => assemblyTools[id]?.run()}
            />
          )
        ) : desktop && tabKind === 'studio' ? (
          <DesktopToolbar
            layout={DESKTOP_TOOLBAR}
            stateOf={studioToolState}
            commands={toolbarCommands}
            busy={editing !== null || dialog !== null || sketch.open || sketch.opening}
            onStart={runTool}
          />
        ) : (
          <ToolStrip
            strip="feature"
            defs={FEATURE_STRIP}
            stateOf={(id) => (id === 'import' && tabKind === 'studio' ? 'ok' : studioToolState(id))}
            disabled={tabKind !== 'studio' || editing !== null || dialog !== null || sketch.open || sketch.opening}
            width={width}
            onStart={(id) => (id === 'import' ? openExplorer('add') : runTool(id))}
          />
        )}
        <div className="shell-main">
          <div className="left-column" data-region="left-column" data-testid="left-column" style={{ width: column }}>
            {tabKind === 'assembly' ? (
              <AssemblyPanel
                collapsed={collapsed}
                locked={dialog !== null || mating}
                outputs={assembly.outputs}
                selected={selectedInstance(picked.items)}
                edits={assembly.edits}
                mates={mateActions}
                onSelect={(i) => selection.set([instanceItem(i.id, i.part)])}
                onContext={onAssemblyRowContext}
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
              partActions={desktop ? { isolate: (id) => void tabView.toggleIsolate([id]), remove: (id) => startTool('delete_part', [{ part: id, kind: 'part' }]) } : undefined}
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
            {mating && mateStart !== null && doc && (
              <MateDialog
                key={mateStart.seq}
                store={store}
                documentId={doc.id}
                documentName={name}
                tab={mateStart.tab}
                start={mateStart}
                types={assemblyTypes}
                outputs={assembly.outputs}
                selection={selection}
                onView={setMateView}
                onDone={endMate}
              />
            )}
            {insertOpen && exploring === null && doc && (
              <InsertPanel
                key={inserting}
                store={store}
                documentId={doc.id}
                tabs={tabs}
                tab={inserting}
                onLibrary={() => openExplorer('add')}
                onInserted={insertedStep}
                note={insertNote}
                edits={assembly.edits}
                onClose={() => setInserting(null)}
              />
            )}
            {exploring !== null && doc && (
              <Explorer
                mode={exploring}
                placement={desktop ? 'float' : 'column'}
                uiMode={mode}
                target={{ document: doc.id, tab: currentTab, studio: tabKind === 'studio' && !sketchHere && editingHere === null, assembly: tabKind === 'assembly', readOnly }}
                currentDocument={doc.id}
                onOpenDocument={(row) => {
                  setExploring(null)
                  back({ id: row.id, name: row.name })
                }}
                onImported={importedFile}
                onInserted={insertedStep}
                onClose={() => setExploring(null)}
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
              <AssemblyViewport outputs={drawnAssembly} selection={selection} drag={mating ? undefined : assembly.edits.drag} visibility={viewState} onContext={onAssemblyContext} />
            ) : (
              <StudioViewport
                outputs={drawn}
                preview={look}
                selection={selection}
                tabKind={tabKind}
                sketch={sketchHere ? sketchStore : null}
                visibility={viewState}
                images={canvasImages}
                onContext={onStudioContext}
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
        {context.node}
        {desktop && paletteOpen && <Palette entries={paletteEntries(paletteEnv)} anchor={viewportEl} onClose={() => setPaletteOpen(false)} />}
        {modal?.kind === 'shortcuts' && <Shortcuts onClose={closeModal} />}
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
        {modal?.kind === 'renameInstance' && (
          <TextPrompt
            title="Rename instance"
            label="Name"
            initial={modal.name}
            confirmLabel="Rename"
            onSubmit={async (value) => {
              const problem = await assembly.edits.rename(modal.id, value)
              if (typeof problem === 'string') return problem
              setModal(null)
            }}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'renameMate' && (
          <TextPrompt
            title="Rename mate"
            label="Name"
            initial={modal.name}
            confirmLabel="Rename"
            onSubmit={async (value) => {
              const problem = await assembly.edits.rename(modal.id, value)
              if (typeof problem === 'string') return problem
              setModal(null)
            }}
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
        {archiveDownload.element}
        {archiveOpen.element}
        {canvasImage.element}
        {modal?.kind === 'leave' && (
          <ConfirmDialog
            title={`Save changes to "${name}"?`}
            message="The Document has changes that are not saved yet. Discard throws them away, and they cannot be recovered afterwards."
            confirm={{ label: 'Save' }}
            alternative={{ label: 'Discard', danger: true, onClick: () => void leave(true, modal.next) }}
            cancelLabel="Stay"
            onConfirm={() => {
              setModal(null)
              void save().then((ok) => {
                if (ok) void leave(false, modal.next)
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

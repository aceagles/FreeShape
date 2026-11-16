import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import type { ConnectionStore } from '../net/connection'
import { addTab, ApiError, closeDocument, deleteTab, fetchDocumentTabs, renameTab, saveDocument, takeOver } from '../net/api'
import type { TabInfo } from '../net/connection'
import { useConnection } from '../net/react'
import { PadDemo, padResult } from '../pad/PadDemo'
import type { PadDemoOptions, PadResult } from '../pad/PadDemo'
import { FeatureDialog } from '../dialog/FeatureDialog'
import type { PreviewState } from '../dialog/useFeatureDialog'
import { AssemblyPanel } from '../assembly/AssemblyPanel'
import { AssemblyViewport } from '../assembly/AssemblyViewport'
import { instanceItem, selectedInstance } from '../assembly/select'
import { useAssembly } from '../assembly/useAssembly'
import { FeaturePanel } from '../features/FeaturePanel'
import { useFeatureEdits } from '../features/useFeatureEdits'
import { useStudioOutputs } from '../features/studioOutputs'
import { SelectionChip } from '../selection/SelectionChip'
import { partItem, SelectionStore } from '../selection/store'
import { useSelection } from '../selection/useSelection'
import { FEATURE_STRIP } from '../tools/defs'
import { picksOf } from '../tools/picks'
import type { Pick } from '../tools/picks'
import { ToolStrip } from '../tools/ToolStrip'
import { stateOf, useToolTypes } from '../tools/useToolTypes'
import { SketchBar } from '../sketch/SketchBar'
import { SketchChip } from '../sketch/SketchChip'
import { SketchStore } from '../sketch/store'
import { ConstraintNote } from '../sketch/ConstraintNote'
import { ConstraintStore, attachDeleteKey, isConstrainTool } from '../sketch/constraints'
import type { SketchTarget } from '../sketch/store'
import { SKETCH_STRIP, sketchToolState } from '../sketch/strip'
import { StudioViewport } from '../viewport/StudioViewport'
import type { PreviewLook } from '../viewport/controller'
import type { StudioOutputs } from '../wire'
import { DialogArea } from './DialogArea'
import { explainError } from './errors'
import { DebugPopover, useDebugPanel } from './LinkStatus'
import { ConfirmDialog } from './Modal'
import { ReadOnlyBanner } from './ReadOnlyBanner'
import { holderPlace } from './holder'
import { TextPrompt } from './TextPrompt'
import { ShellContext } from './ShellContext'
import type { ShellApi } from './ShellContext'
import { TopBar } from './TopBar'
import { columnWidth } from './layout'
import { tokenVars } from './vars'
import './shell.css'

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
}

/** The small dialog over the page, if one is open. */
type Modal =
  | { kind: 'rename'; tab: TabInfo }
  | { kind: 'delete'; tab: TabInfo }
  | { kind: 'leave' }
  | { kind: 'takeover' }
  | { kind: 'message'; title: string; text: string }

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
export function Shell({ store, documentId, version, onLeave }: { store: ConnectionStore; documentId: string; version?: string; onLeave: () => void }) {
  const { tabs, document: doc, readOnly, holder, client } = useConnection(store)
  const [selected, setSelected] = useState<string | null>(null)
  // One selection for the view, the chip and the Parts list; a part is one kind of item in it (see ShellApi.selection)
  const [selection] = useState(() => new SelectionStore())
  const picked = useSelection(selection)
  const [pickedFeature, setPickedFeature] = useState<{ tab: string; id: string; revision: number } | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [dialog, setDialog] = useState<DialogState | null>(null)
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
  useEffect(() => {
    constraints.attach()
    const off = attachDeleteKey(constraints)
    return () => {
      off()
      constraints.detach()
    }
  }, [constraints])
  const constraintState = useSyncExternalStore(constraints.subscribe, constraints.getState)
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
  const column = columnWidth(width, collapsed)
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
  }, [currentTab, selection])

  const api = useMemo<ShellApi>(
    () => ({
      openDialog: (title, content) => {
        if (editing !== null) return
        setCollapsed(false)
        setDialog({ title, content })
      },
      closeDialog: () => setDialog(null),
      dialogOpen: dialog !== null || editing !== null,
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
    [dialog, editing, collapsed, selectedFeature, selectedPart, currentTab, selection],
  )
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
      setCollapsed(false)
      setPreview(null)
      setDialogBase(null)
      setHoldUntil(null)
      setEditing({ tab: currentTab, id: feature.id, name: feature.name })
    },
    [currentTab, dialog],
  )
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
    (type: string) => {
      if (currentTab === null || tabKind !== 'studio' || editing !== null || dialog !== null) return
      setCollapsed(false)
      setPreview(null)
      setHoldUntil(null)
      setEditing({ tab: currentTab, id: '', name: toolTypes?.get(type)?.label ?? type, create: { type, picks } })
      // the tool took the selection; the dialog's active field takes the next picks
      selection.clear()
    },
    [currentTab, tabKind, editing, dialog, toolTypes, picks, selection],
  )
  const showPreview = preview !== null && (editingHere !== null || (holdUntil !== null && (studio?.revision ?? 0) < holdUntil))
  // while a dialog is open the view draws its base (what picks resolve against) and the preview as a ghost over it;
  // until the base has arrived, and after Accept, the preview itself is drawn as before
  const base = editingHere !== null && dialogBase !== null && dialogBase.tab === editingHere.tab ? dialogBase : null
  const drawn = base ?? (showPreview ? preview.outputs : studio)
  const look: PreviewLook | null = showPreview ? { number: preview.number, feature: preview.feature, ghost: base === null ? null : preview.outputs } : null
  const variables = useMemo(() => Object.keys(studio?.variables ?? {}).map((n) => n.replace(/^#/, '')), [studio])
  const assembly = useAssembly({ store, tab: currentTab, tabKind, readOnly, documentName: name, onError: failText })

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

  return (
    <ShellContext.Provider value={api}>
      <div className="shell" style={tokenVars()} data-testid="shell">
        <TopBar
          store={store}
          currentTab={currentTab}
          readOnly={readOnly}
          saving={saving}
          canUndo={studio?.canUndo === true && !edits.busy && !editingHere && !sketch.open}
          canRedo={studio?.canRedo === true && !edits.busy && !editingHere && !sketch.open}
          onUndo={edits.undo}
          onRedo={edits.redo}
          debugOpen={debug.open}
          onSelectTab={setSelected}
          onAddTab={(kind) => void add(kind)}
          onRenameTab={(tab) => setModal({ kind: 'rename', tab })}
          onDeleteTab={(tab) => setModal({ kind: 'delete', tab })}
          onBack={back}
          onSave={() => void save()}
          onToggleDebug={debug.toggle}
        />
        {readOnly && <ReadOnlyBanner name={name} holder={holder} onTakeOver={() => setModal({ kind: 'takeover' })} />}
        {sketchHere ? (
          <ToolStrip
            strip="sketch"
            defs={SKETCH_STRIP}
            stateOf={(id) => (isConstrainTool(id) ? (constraintState.enabled.has(id) ? 'ok' : 'dimmed') : sketchToolState(id))}
            width={width}
            active="select"
            onStart={(id) => constraints.apply(id)}
            trailing={
              <SketchBar
                nav={sketch.nav}
                onNav={(n) => sketchStore.setNav(n)}
                onNormalTo={() => sketchStore.normalTo()}
                onCancel={() => sketchStore.cancel()}
                onDone={() => sketchStore.commit()}
              />
            }
          />
        ) : (
          <ToolStrip
            strip="feature"
            defs={FEATURE_STRIP}
            stateOf={(id) => stateOf(toolTypes, id)}
            disabled={tabKind !== 'studio' || editing !== null || dialog !== null || sketch.open || sketch.opening}
            width={width}
            onStart={startTool}
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
              locked={dialog !== null || editingHere !== null || sketchHere}
              tabKind={tabKind}
              outputs={studio}
              selected={selectedFeature}
              selectedPart={selectedPart}
              edits={panelEdits}
              onEdit={studio !== null && !editingHere && dialog === null ? startEdit : undefined}
              onSelect={api.selectFeature}
              onSelectPart={api.selectPart}
              onCollapsedChange={setCollapsed}
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
            {dialog && (
              <DialogArea title={dialog.title} onClose={() => setDialog(null)}>
                {dialog.content}
              </DialogArea>
            )}
          </div>
          {/* data-dialog-base: the view draws an open dialog's base model, the one its picks resolve against (a test hook) */}
          <div className="shell-viewport" data-region="viewport" data-testid="shell-viewport" data-dialog-base={base !== null ? '' : undefined}>
            {tabKind === 'assembly' ? (
              <AssemblyViewport outputs={assembly.outputs} selection={selection} drag={assembly.edits.drag} />
            ) : (
              <StudioViewport
                outputs={drawn}
                preview={look}
                selection={selection}
                tabKind={tabKind}
                sketch={sketchHere ? sketchStore : null}
              />
            )}
            <div className="chip-slot" data-testid="chip-slot" data-region="selection-chip">
              {sketchHere ? <SketchChip store={sketchStore} /> : <SelectionChip store={selection} />}
              {sketchHere && <ConstraintNote store={constraints} />}
            </div>
          </div>
        </div>
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
        {modal?.kind === 'takeover' && (
          <ConfirmDialog
            title={`Take over "${name}"?`}
            message={`${holderPlace(holder)[0]!.toUpperCase()}${holderPlace(holder).slice(1)} will lose the right to edit it, and what it has not saved is not kept here.`}
            confirm={{ label: 'Take over', danger: true }}
            onConfirm={() => void doTakeOver()}
            onCancel={closeModal}
          />
        )}
        {modal?.kind === 'message' && (
          <ConfirmDialog title={modal.title} message={modal.text} confirm={{ label: 'OK' }} cancelLabel={null} onConfirm={closeModal} onCancel={closeModal} />
        )}
      </div>
    </ShellContext.Provider>
  )
}

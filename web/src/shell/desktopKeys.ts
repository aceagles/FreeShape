import type { KeyCommand } from '../input/keys'
import type { DialogKeys } from '../dialog/FeatureDialog'
import type { SelectionItem, SelectionStore } from '../selection/store'
import type { ConstraintStore } from '../sketch/constraints'
import type { SketchStore } from '../sketch/store'
import { escapeSketch, sketchKeyCommands } from '../sketch/desktopSketchKeys'
import type { ViewHandler } from '../viewport/viewCommands'

/**
 * What the desktop's keys read and act on, as the shell has it at the moment of the key (card I9.6). The shell builds it
 * on every render; `desktopCommands` turns it into the keymap's commands.
 */
export interface DesktopKeyContext {
  readOnly: boolean
  /** The kind of the tab on screen (studio, assembly, ...), or null with no tab. */
  tabKind: string | null
  /** The sketch open on the tab on screen, or null. */
  sketch: SketchStore | null
  constraints: ConstraintStore
  /** The feature dialog open on the tab on screen, or null (read at the key: the dialog registers itself after it is drawn). */
  dialog: () => DialogKeys | null
  /** Another dialog in the dialog area (not a feature dialog) is open: Esc closes it. */
  closeArea: (() => void) | null
  /** A small dialog over the page (a confirmation, a prompt) has the keyboard: no key acts here. */
  modalOpen: boolean
  selection: SelectionStore
  /** The feature selected in the feature list, or null. */
  selectedFeature: { id: string; name: string } | null
  /** The Assembly instance selected, or null. */
  selectedInstance: string | null
  /** Run a menu command when it is wired and on (shell/menus.ts); false when it is not. */
  runMenu: (command: string) => boolean
  /** Undo and Redo as the top bar and the toolbar have them: the open sketch's, else the Part Studio's. */
  undo: { enabled: boolean; run: () => void }
  redo: { enabled: boolean; run: () => void }
  /** The Assembly toolbar's Undo and Redo (`assembly.edits`): the same functions, so the key and the button cannot differ. */
  assemblyUndo: () => void
  assemblyRedo: () => void
  /** The Sketch tool: on the selected plane or planar face, else the plane chooser. */
  beginSketch: () => void
  /** Open a feature's create dialog with the selection as its picks. */
  startTool: (type: string) => void
  /** Shift+E / Shift+W in a sketch: finish it, then open the feature with the sketch's regions picked. */
  featureFromSketch: (type: string) => void
  /** Leave the open sketch without keeping it, asking first when something was drawn. */
  leaveSketch: () => void
  /** Ask, then delete the feature (the feature list's Delete). */
  deleteFeature: (feature: { id: string; name: string }) => void
  /** Ask, then delete the Assembly instance (the desktop's `delete_instances`). */
  deleteInstance: (instance: string) => void
  /** Drop the feature list's selected feature. */
  clearFeature: () => void
  /** The 3D view on screen. */
  view: () => ViewHandler | null
  /** The visibility keys' commands (shell/visibilityCommands.ts); each returns false when it does not act here. */
  visibility: Record<string, () => boolean>
}

/** Space and Enter on a focused button press the button, as the browser does; the keymap leaves them to it. */
const onButton = (): boolean => {
  const a = document.activeElement
  if (!(a instanceof HTMLElement && (a.tagName === 'BUTTON' || a.tagName === 'A' || a.getAttribute('role') === 'button'))) return false
  // a button a mouse click focused (Chromium does; WebKit may) is no reason to give it the key: the click's focus is let go
  if (mouseFocus) {
    a.blur()
    return false
  }
  return true
}
/** The last thing that moved the focus was a mouse press, not Tab: set by the two capture listeners below. */
let mouseFocus = false
if (typeof window !== 'undefined') {
  window.addEventListener('pointerdown', (e) => (mouseFocus = e.pointerType === 'mouse'), true)
  window.addEventListener('keydown', (e) => e.key === 'Tab' && (mouseFocus = false), true)
}

const FLAT: ReadonlySet<string> = new Set(['face', 'plane', 'region'])

/**
 * The commands of Desktop mode's keys (input/bindings.ts names them), routed per workspace as the desktop routes them:
 * `MainWindow._escape` and `_delete_key` for Esc and Delete, `_feature_from_sketch` for Shift+E and Shift+W, `_normal_key`
 * for N, `Viewport.keyPressEvent` for Space and the arrows. A command that returns false leaves the key to the browser.
 */
export function desktopCommands(c: DesktopKeyContext): Record<string, KeyCommand> {
  const studio = c.tabKind === 'studio'
  const sk = c.sketch
  const idle = (): boolean => sk === null && c.dialog() === null && c.closeArea === null
  // the same Undo and Redo as the top bar's buttons; in a Part Studio only with nothing open over it
  const undo = (redo: boolean) => (): boolean => {
    // on an Assembly tab (MainWindow.undo with the Assembly showing): the Assembly's step; nothing in a read-only Document
    if (c.tabKind === 'assembly') {
      if (!c.readOnly && idle()) (redo ? c.assemblyRedo : c.assemblyUndo)()
      return true
    }
    const cmd = redo ? c.redo : c.undo
    if (cmd.enabled && (sk !== null || (studio && idle() && !c.readOnly))) cmd.run()
    return true
  }
  const fromSketch = (type: string) => (): boolean => {
    if (c.readOnly || !studio) return false
    if (sk !== null) c.featureFromSketch(type)
    else if (idle()) c.startTool(type)
    return true
  }
  const turn = (h: number, v: number) => (): boolean => c.view()?.turnStep?.(h, v) ?? false
  const pan = (sx: number, sy: number) => (): boolean => c.view()?.panStep?.(sx, sy) ?? false
  const menu = (command: string) => (): boolean => c.runMenu(command)
  const commands: Record<string, KeyCommand> = {
    'edit.undo': undo(false),
    'edit.redo': undo(true),
    'document.save': menu('document.save'),
    'document.newStudio': menu('document.newStudio'),
    'document.newAssembly': menu('document.newAssembly'),
    'document.closeTab': menu('document.closeTab'),
    'document.open': menu('document.open'),
    'feature.sketch': () => {
      if (!studio || c.readOnly || !idle()) return false
      c.beginSketch()
      return true
    },
    'feature.extrude': fromSketch('extrude'),
    'feature.revolve': fromSketch('revolve'),
    // in an Assembly Shift+F is the Fastened mate (card I10.6): nothing yet
    'feature.fillet': () => {
      if (!studio || c.readOnly || !idle()) return false
      c.startTool('fillet')
      return true
    },
    'view.normal': () => {
      if (sk !== null) {
        sk.normalTo()
        return true
      }
      if (!studio) return false
      const aimed = c.selection.getAimed()
      const item: SelectionItem | null =
        aimed !== null && FLAT.has(aimed.kind) ? aimed : (c.selection.getState().items.find((i) => FLAT.has(i.kind)) ?? null)
      return c.view()?.normalKey?.(item) ?? false
    },
    'view.fit': menu('view.fit'),
    'view.front': menu('view.front'),
    'view.back': menu('view.back'),
    'view.left': menu('view.left'),
    'view.right': menu('view.right'),
    'view.top': menu('view.top'),
    'view.bottom': menu('view.bottom'),
    'view.iso': menu('view.iso'),
    'view.turnLeft': turn(1, 0),
    'view.turnRight': turn(-1, 0),
    'view.turnUp': turn(0, 1),
    'view.turnDown': turn(0, -1),
    'view.panLeft': pan(-1, 0),
    'view.panRight': pan(1, 0),
    'view.panUp': pan(0, -1),
    'view.panDown': pan(0, 1),
    // P, Shift+P, Y, Shift+Y, Shift+I, Shift+T, K, J, Shift+H: the commands the View menu calls too (card I9.8)
    ...c.visibility,
    'select.clear': () => {
      if (onButton()) return false
      if (sk !== null) sk.clearSelection()
      else {
        c.selection.clear()
        c.clearFeature()
      }
      return true
    },
    // _delete_key: a sketch deletes its selection; an open dialog takes nothing; an Assembly deletes the selected instance;
    // a Part Studio the selected feature, else the selected parts through the Delete part dialog
    delete: () => {
      if (c.readOnly) return false
      if (sk !== null) {
        if (!c.constraints.removeSelected()) sk.deleteSelected()
        return true
      }
      if (!idle()) return true
      if (c.tabKind === 'assembly') {
        if (c.selectedInstance === null) return false
        c.deleteInstance(c.selectedInstance)
        return true
      }
      if (!studio) return false
      if (c.selectedFeature !== null) {
        c.deleteFeature(c.selectedFeature)
        return true
      }
      if (!c.selection.getState().items.some((i) => i.kind === 'part')) return false
      c.startTool('delete_part')
      return true
    },
    // _escape: a dialog is cancelled; an Assembly drops its selection; a sketch steps back (a constraint's picks first) and,
    // with nothing left to step back, is left; a Part Studio drops its selection
    cancel: () => {
      const dialog = c.dialog()
      if (dialog !== null) {
        dialog.cancel()
        return true
      }
      if (c.closeArea !== null) {
        c.closeArea()
        return true
      }
      if (sk !== null) {
        if (!escapeSketch(sk, c.constraints)) c.leaveSketch()
        return true
      }
      c.selection.clear()
      c.clearFeature()
      return true
    },
    'dialog.accept': () => {
      const dialog = c.dialog()
      return dialog !== null && !onButton() ? dialog.accept() : false
    },
  }
  // the sketch's own keys (card I9.7: the tool keys, Q, Ctrl+A), only while a sketch is open here
  if (sk !== null) Object.assign(commands, sketchKeyCommands(sk, c.constraints))
  if (!c.modalOpen) return commands
  // a small dialog over the page has the keyboard (it takes Esc itself): no key acts behind it
  return {}
}

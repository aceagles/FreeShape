import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { SelectionStore } from '../selection/store'
import { STANDARD_VIEWS, ViewCamera } from '../viewport/camera'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs } from '../wire'
import { GizmoController, livePose, rotationMotion, translationMotion } from './gizmo'
import type { DragSender, GizmoShape, LiveDrag } from './gizmo'
import { instanceItem } from './select'
import { AssemblyLoader, makeDragSender } from './useAssembly'

// F17: an Assembly drag shows at once. The dragged instance follows the pointer before the server answers, move_to replies
// bring the solved placements, and the outputs fetched while revisions keep rising are shown, not dropped.

function hinge(): AssemblyOutputs {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
  if (!isAssembly(out)) throw new Error('the fixture is not an Assembly')
  return out
}

const close = (A: number[][], B: readonly (readonly number[])[]) => A.forEach((r, i) => r.forEach((v, j) => expect(v).toBeCloseTo(B[i]![j]!, 9)))
const mul = (A: readonly (readonly number[])[], B: readonly (readonly number[])[]) =>
  [0, 1, 2, 3].map((i) => [0, 1, 2, 3].map((j) => [0, 1, 2, 3].reduce((s, k) => s + A[i]![k]! * B[k]![j]!, 0)))
const I = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
]

function deferred<T>() {
  let res!: (v: T) => void
  let rej!: (e: unknown) => void
  const promise = new Promise<T>((a, b) => {
    res = a
    rej = b
  })
  return { promise, resolve: res, reject: rej }
}
const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve()
}

describe('livePose', () => {
  const T0 = mul(translationMotion([0, 0, 1], 7), rotationMotion([0, 1, 0], 0.4, [1, 2, 3]))
  const turn = (deg: number) => rotationMotion([0, 0, 1], (deg * Math.PI) / 180, [5, 0, 0])

  it('before any answer is the rigid motion applied to where the drag began', () => {
    close(livePose(turn(20), I, T0), mul(turn(20), T0))
  })

  it('after an answer the mates allowed, the instance still sits at the motion now applied to where it began', () => {
    close(livePose(turn(35), turn(20), mul(turn(20), T0)), mul(turn(35), T0))
  })

  it('after an answer the mates refused, only the motion not yet answered shows', () => {
    const slide = translationMotion([1, 0, 0], 10)
    close(livePose(slide, slide, T0), T0)
    close(livePose(translationMotion([1, 0, 0], 12), slide, T0), mul(translationMotion([1, 0, 0], 2), T0))
  })
})

describe('the gizmo with a live view', () => {
  function rig() {
    const outputs = hinge()
    const view = new ViewCamera()
    view.resize(800, 600)
    view.setBack(STANDARD_VIEWS.front)
    view.scale = 0.5
    view.target.set(0, 0, 0)
    const selection = new SelectionStore()
    const inst = outputs.instances.find((i) => i.id === 'I2')!
    selection.set([instanceItem(inst.id, inst.part)])
    const calls: string[] = []
    const motions: number[][][] = []
    let drawn: number[][] | null = null
    const live: LiveDrag = {
      pose: (id) => (id === 'I2' ? drawn : null),
      begin: (id) => void calls.push(`begin ${id}`),
      motion: (id, T) => {
        calls.push(`motion ${id}`)
        motions.push(T)
        drawn = mul(T, inst.T)
      },
      done: () => void calls.push('done'),
    }
    const end = deferred<void>()
    const sender: DragSender = {
      disabled: false,
      begin: () => void calls.push('send begin'),
      move: () => void calls.push('send move'),
      end: () => (calls.push('send end'), end.promise),
      listen: () => {},
    }
    let shape: GizmoShape | null = null
    const gizmo = new GizmoController(view, selection, (s) => (shape = s), live)
    gizmo.setSender(sender)
    gizmo.setOutputs(outputs)
    return { gizmo, calls, motions, end, shape: () => shape }
  }

  it('hands every pointer move to the view before the server has answered, and says done once it has', async () => {
    const r = rig()
    const x = r.shape()!.axes[0]!
    const from = x.move!
    const at = (k: number) => ({ x: from.x + x.dir!.x * 10 * k, y: from.y + x.dir!.y * 10 * k, pressure: 1, pointerId: 1 })
    expect(r.gizmo.press(at(0), 'mouse')).toBe(true)
    r.gizmo.move(at(1))
    r.gizmo.move(at(2))
    expect(r.calls).toEqual(['begin I2', 'send begin', 'motion I2', 'send move', 'motion I2', 'send move'])
    // 20 px at 0.5 mm per px: the instance is drawn 10 mm along its X axis already
    expect(Math.hypot(r.motions[1]![0]![3]!, r.motions[1]![1]![3]!, r.motions[1]![2]![3]!)).toBeCloseTo(10, 6)
    r.gizmo.release(at(2))
    expect(r.calls.at(-1)).toBe('send end')
    r.end.resolve()
    await flush()
    expect(r.calls.at(-1)).toBe('done')
  })

  it('draws the gizmo where the instance is drawn, so it moves with the drag', () => {
    const r = rig()
    const before = r.shape()!.origin
    const x = r.shape()!.axes[0]!
    const from = x.move!
    r.gizmo.press({ x: from.x, y: from.y, pressure: 1, pointerId: 1 }, 'mouse')
    r.gizmo.move({ x: from.x + x.dir!.x * 40, y: from.y + x.dir!.y * 40, pressure: 1, pointerId: 1 })
    const after = r.shape()!.origin
    expect(Math.hypot(after.x - before.x, after.y - before.y)).toBeCloseTo(40, 3)
  })
})

describe('the drag sender', () => {
  it('keeps one move_to in flight, sends the newest next, and hands each reply\'s placements over with the motion it answered', async () => {
    const posted: Record<string, unknown>[] = []
    const replies: ReturnType<typeof deferred<unknown>>[] = []
    const post = (edit: Record<string, unknown>) => {
      posted.push(edit)
      const d = deferred<unknown>()
      replies.push(d)
      return d.promise
    }
    const heard: [Record<string, number[][]>, number[][]][] = []
    const s = makeDragSender(post, () => {}, false)
    s.listen((p, T) => heard.push([p, T]))
    const T = (x: number) => translationMotion([1, 0, 0], x)
    s.begin('I2')
    replies[0]!.resolve({ rebuilt: ['T2'] })
    await flush()
    s.move(T(1))
    await flush()
    s.move(T(2))
    s.move(T(3))
    await flush()
    expect(posted.map((e) => e['op'])).toEqual(['move_begin', 'move_to'])
    replies[1]!.resolve({ rebuilt: ['T2'], placements: { I2: T(1) } })
    await flush()
    expect(heard).toEqual([[{ I2: T(1) }, T(1)]])
    // the waiting T(2) was replaced by T(3)
    expect(posted.map((e) => e['T'] ?? e['op'])).toEqual(['move_begin', T(1), T(3)])
    // a reply with no placements (an older server) says nothing
    replies[2]!.resolve({ rebuilt: ['T2'] })
    const ended = s.end()
    await flush()
    replies[3]!.resolve({ rebuilt: ['T2'] })
    await ended
    expect(heard.length).toBe(1)
    expect(posted.at(-1)).toEqual({ op: 'move_end' })
  })
})

describe('AssemblyLoader', () => {
  function rig() {
    const asked: ReturnType<typeof deferred<AssemblyOutputs | null>>[] = []
    const shown: number[] = []
    const loader = new AssemblyLoader(
      () => {
        const d = deferred<AssemblyOutputs | null>()
        asked.push(d)
        return d.promise
      },
      (o) => shown.push(o.revision),
    )
    const at = (revision: number, tab = 'T2') => ({ ...hinge(), tab, revision })
    return { loader, asked, shown, at }
  }

  it('keeps one fetch in flight while revisions rise, shows its answer, then fetches once more', async () => {
    const r = rig()
    r.loader.reset('D', 'T2')
    r.asked[0]!.resolve(r.at(1))
    await flush()
    r.loader.load('D', 'T2')
    r.loader.load('D', 'T2')
    r.loader.load('D', 'T2')
    expect(r.asked.length).toBe(2)
    r.asked[1]!.resolve(r.at(4))
    await flush()
    // the answer in flight is shown although newer revisions were heard meanwhile; one more fetch follows
    expect(r.shown).toEqual([1, 4])
    expect(r.asked.length).toBe(3)
    r.asked[2]!.resolve(r.at(6))
    await flush()
    expect(r.shown).toEqual([1, 4, 6])
    expect(r.asked.length).toBe(3)
  })

  it('never shows an answer older than the outputs on screen, nor one for a tab asked before a reset', async () => {
    const r = rig()
    r.loader.reset('D', 'T2')
    r.asked[0]!.resolve(r.at(5))
    await flush()
    r.loader.load('D', 'T2')
    r.asked[1]!.resolve(r.at(3))
    await flush()
    expect(r.shown).toEqual([5])
    r.loader.load('D', 'T2')
    r.loader.reset('D', 'T4')
    r.asked[2]!.resolve(r.at(9))
    r.asked[3]!.resolve(r.at(1, 'T4'))
    await flush()
    expect(r.shown).toEqual([5, 1])
  })
})

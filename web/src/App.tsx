import { useCallback, useEffect, useState } from 'react'
import { fetchHealth } from './health'
import { Home } from './home/Home'
import { listDocuments, openDocument } from './net/api'
import type { DocumentReply } from './net/api'
import type { ConnectionStore } from './net/connection'
import { useConnection } from './net/react'
import { Shell } from './shell/Shell'

type Screen = { kind: 'starting' } | { kind: 'home'; notice?: string } | { kind: 'document'; id: string; name: string }

/**
 * The client: the home screen (src/home) and, inside a Document, the app shell (src/shell), over the live connection.
 *
 * It starts straight in a Document when the server has exactly one that is already open, or just one Document at
 * all and it is open: that is a server started on one Document folder, and the home screen would be a pointless
 * extra tap there. It does the same after a reload when the Documents root has one Document open. In every other
 * case it starts on the home screen. The server version from /health shows in the debug panel.
 */
export function App({ store }: { store: ConnectionStore }) {
  const [version, setVersion] = useState<string | undefined>()
  const [screen, setScreen] = useState<Screen>({ kind: 'starting' })
  const { ended } = useConnection(store)

  useEffect(() => {
    let live = true
    fetchHealth().then(
      (h) => live && setVersion(h.version),
      () => {},
    )
    return () => {
      live = false
    }
  }, [])

  const enter = useCallback(
    (reply: DocumentReply) => {
      store.watch(reply.document.id, reply)
      setScreen({ kind: 'document', id: reply.document.id, name: reply.document.name })
    },
    [store],
  )

  useEffect(() => {
    let live = true
    listDocuments()
      .then(async (rows) => {
        const open = rows.filter((r) => r.open && !r.missing)
        const resume = rows.length === 1 ? open[0] : open.length === 1 ? open[0] : undefined
        if (!resume) return false
        const reply = await openDocument(resume.id)
        if (live) enter(reply)
        return true
      })
      .then(
        (entered) => live && !entered && setScreen({ kind: 'home' }),
        () => live && setScreen({ kind: 'home' }),
      )
    return () => {
      live = false
    }
  }, [enter])

  useEffect(() => {
    if (ended && screen.kind === 'document') {
      store.unwatch()
      const text = ended === 'closed' ? 'was closed on the server' : 'is no longer open on the server'
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setScreen({ kind: 'home', notice: `"${screen.name}" ${text}. Open it again to carry on.` })
    }
  }, [ended, screen, store])

  const leave = useCallback(() => {
    store.unwatch()
    setScreen({ kind: 'home' })
  }, [store])

  if (screen.kind === 'document') return <Shell store={store} documentId={screen.id} version={version} onLeave={leave} />
  if (screen.kind === 'home') return <Home store={store} version={version} notice={screen.notice} onOpened={enter} />
  return <div className="app-starting" data-testid="starting" />
}

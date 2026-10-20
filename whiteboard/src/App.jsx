import React, { useEffect, useMemo, useState } from 'react'
import { Tldraw, toRichText } from 'tldraw'
import { useSync } from '@tldraw/sync'
import 'tldraw/tldraw.css'
import './style.css'

const ASSETS = {
  // Notes, text, drawings and shapes work. Binary file uploads are NOT in this MVP.
  upload: async () => { throw new Error('Binary asset uploads are not enabled in this MVP.') },
  resolve: (asset) => asset.props?.src ?? null,
}

function EditorBoard({ boardId }) {
  const [title, setTitle] = useState('Whiteboard')
  const [loadError, setLoadError] = useState('')
  const [editor, setEditor] = useState(null)
  const wsUrl = useMemo(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${window.location.host}/connect/${boardId}`
  }, [boardId])

  const store = useSync({ uri: wsUrl, assets: ASSETS })

  useEffect(() => {
    let canceled = false
    fetch(`/api/boards/${boardId}`, { cache: 'no-store' })
      .then(async (res) => {
        if (!res.ok) throw new Error('Board not found or server unavailable')
        return res.json()
      })
      .then((board) => { if (!canceled) setTitle(board.title) })
      .catch((error) => { if (!canceled) setLoadError(error.message) })
    return () => { canceled = true }
  }, [boardId])

  function addNote() {
    if (!editor) return
    const center = editor.getViewportPageBounds().center
    editor.createShape({
      type: 'note',
      x: center.x - 100,
      y: center.y - 100,
      props: { richText: toRichText('New idea'), color: 'yellow' },
    })
  }

  return (
    <div className="app-shell">
      <header className="toolbar">
        <div className="branding">
          <span className="logo">✳</span>
          <span className="title" title={title}>{title}</span>
          <span className="hint">Live collaborative board</span>
        </div>
        <div className="toolbar-actions">
          <button onClick={addNote} disabled={!editor}>+ Add sticky note</button>
          <button className="secondary" onClick={() => navigator.clipboard.writeText(window.location.href)}>
            Copy link
          </button>
        </div>
      </header>
      {loadError && <div className="error-banner">{loadError}</div>}
      <div className="editor-area">
        <Tldraw store={store} licenseKey={import.meta.env.VITE_TLDRAW_LICENSE_KEY}/>
      </div>
      <footer className="footer">
        Changes sync and save automatically. Anyone with this link can edit.
      </footer>
    </div>
  )
}

export default function App() {
  const boardId = window.location.pathname.match(/^\/b\/([a-f0-9-]{36})\/?$/i)?.[1]
  if (!boardId) {
    return <main className="welcome"><h1>Discord Whiteboard</h1><p>Create a board from your Discord server using <code>/tldraw</code>.</p></main>
  }
  return <EditorBoard boardId={boardId} />
}

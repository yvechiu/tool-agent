/** Extract only sticky note content from tldraw RoomSnapshot.documents. */
export function richTextToPlainText(value) {
  if (!value) return ''
  if (typeof value === 'string') return value.trim()
  const parts = []
  function walk(node) {
    if (!node || typeof node !== 'object') return
    if (typeof node.text === 'string') parts.push(node.text)
    if (node.type === 'hardBreak') parts.push('\n')
    if (Array.isArray(node.content)) {
      for (const child of node.content) walk(child)
      if (node.type === 'paragraph' || node.type === 'heading') parts.push('\n')
    }
  }
  walk(value)
  return parts.join('').replace(/\n{3,}/g, '\n\n').trim()
}

export function extractStickyNotes(snapshot) {
  const docs = Array.isArray(snapshot?.documents) ? snapshot.documents : []
  return docs
    .map((entry) => entry?.state)
    .filter((record) => record?.typeName === 'shape' && record?.type === 'note')
    .map((note) => ({
      id: note.id,
      content: richTextToPlainText(note.props?.richText ?? note.props?.text),
      color: note.props?.color ?? null,
      position: { x: note.x ?? 0, y: note.y ?? 0 },
      parent_id: note.parentId ?? null,
    }))
}

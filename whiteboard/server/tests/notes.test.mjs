import { test } from 'node:test'
import assert from 'node:assert/strict'
import { extractStickyNotes, richTextToPlainText } from '../src/notes.mjs'

test('rich text paragraphs and hard breaks', () => {
  const richText = { type: 'doc', content: [
    { type: 'paragraph', content: [{ type: 'text', text: 'Hello' }, { type: 'hardBreak' }, { type: 'text', text: 'world' }] },
    { type: 'paragraph', content: [{ type: 'text', text: 'Next' }] },
  ] }
  assert.equal(richTextToPlainText(richText), 'Hello\nworld\nNext')
})

test('extracts only note shapes from RoomSnapshot documents', () => {
  const snapshot = { documents: [
    { lastChangedClock: 2, state: { typeName: 'shape', id: 'shape:1', type: 'note', x: 10, y: 20, parentId: 'page:main', props: { color: 'yellow', richText: { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'Idea A' }] }] } } } },
    { lastChangedClock: 3, state: { typeName: 'shape', id: 'shape:2', type: 'geo', props: { color: 'blue' } } },
  ] }
  assert.deepEqual(extractStickyNotes(snapshot), [
    { id: 'shape:1', content: 'Idea A', color: 'yellow', position: { x: 10, y: 20 }, parent_id: 'page:main' },
  ])
})

import { randomUUID, timingSafeEqual } from 'node:crypto'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import Fastify from 'fastify'
import websocket from '@fastify/websocket'
import staticPlugin from '@fastify/static'
import dotenv from 'dotenv'

import { extractStickyNotes } from './notes.mjs'
import { RoomRegistry } from './rooms.mjs'

const whiteboardDir = fileURLToPath(new URL('../../', import.meta.url))

// Local development: whiteboard/.env; Fly secrets are injected via process.env.
dotenv.config({ path: path.join(whiteboardDir, '.env') })

const apiKey = (process.env.TLDRAW_SERVICE_API_KEY || '').trim()

if (!apiKey || apiKey.length < 20 || apiKey.includes('replace_with_')) {
  throw new Error('TLDRAW_SERVICE_API_KEY must be a random secret of at least 20 characters')
}

const publicBase = (process.env.TLDRAW_PUBLIC_URL || 'https://discord-whiteboard.fly.dev').replace(/\/$/, '')
const port = Number(process.env.TLDRAW_PORT || 5858)

/** Ephemeral data: intentionally lost when this process exits. */
const boards = new Map()
const rooms = new RoomRegistry()

const app = Fastify({ logger: true, bodyLimit: 64 * 1024 })

await app.register(websocket)

function validBoardId(value) {
  return typeof value === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}

function hasKey(req) {
  const received = req.headers['x-api-key']

  if (typeof received !== 'string') return false

  const a = Buffer.from(received)
  const b = Buffer.from(apiKey)

  return a.length === b.length && timingSafeEqual(a, b)
}

async function requireKey(req, reply) {
  if (!hasKey(req)) {
    return reply.code(401).send({ error: 'Unauthorized' })
  }
}

// ------------------------------------------------
// Health Check
// ------------------------------------------------

app.get('/api/healthz', async () => ({
  ok: true,
  service: 'tldraw-whiteboard'
}))

// ------------------------------------------------
// Create Board
// POST /api/boards
// ------------------------------------------------

app.post('/api/boards', { preHandler: requireKey }, async (req, reply) => {
  const title = typeof req.body?.title === 'string' ? req.body.title.trim() : ''

  if (title.length < 1 || title.length > 60) {
    return reply.code(400).send({ error: 'Title must be 1-60 characters' })
  }

  const id = randomUUID()

  const board = {
    id,
    title,
    created_at: new Date().toISOString()
  }

  boards.set(id, board)

  return reply.code(201).send({
    ...board,
    url: `${publicBase}/b/${id}`
  })
})

// ------------------------------------------------
// Get Board Metadata
// GET /api/boards/:boardId
// ------------------------------------------------

// In this MVP, knowing the unguessable board URL is the editing capability.
app.get('/api/boards/:boardId', async (req, reply) => {
  const { boardId } = req.params

  if (!validBoardId(boardId) || !boards.has(boardId)) {
    return reply.code(404).send({ error: 'Board not found' })
  }

  reply.header('Cache-Control', 'no-store')

  return boards.get(boardId)
})

// ------------------------------------------------
// Get Sticky Notes
// GET /api/boards/:boardId/notes
// ------------------------------------------------

app.get('/api/boards/:boardId/notes', { preHandler: requireKey }, async (req, reply) => {
  const { boardId } = req.params

  const board = boards.get(boardId)

  if (!validBoardId(boardId) || !board) {
    return reply.code(404).send({ error: 'Board not found' })
  }

  const snapshot = rooms.get(boardId).storage.getSnapshot()

  const notes = extractStickyNotes(snapshot)

  reply.header('Cache-Control', 'no-store')

  return {
    board_id: boardId,
    title: board.title,
    notes
  }
})

// ------------------------------------------------
// WebSocket Collaboration
// GET /connect/:boardId
// ------------------------------------------------

app.get('/connect/:boardId', { websocket: true }, (socket, req) => {
  const { boardId } = req.params
  const sessionId = req.query?.sessionId

  if (!validBoardId(boardId) || !boards.has(boardId)) {
    socket.close(1008, 'Board not found')
    return
  }

  if (typeof sessionId !== 'string' || !sessionId || sessionId.length > 200) {
    socket.close(1008, 'Invalid session ID')
    return
  }

  // Attach a listener immediately so early WebSocket handshake frames are not lost.
  const queued = []
  const collect = (message) => queued.push(message)

  socket.on('message', collect)

  try {
    const room = rooms.get(boardId)

    room.handleSocketConnect({ sessionId, socket })

    socket.off('message', collect)

    for (const message of queued) {
      socket.emit('message', message)
    }

  } catch (error) {
    socket.off('message', collect)

    app.log.error(error)

    socket.close(1011, 'Unable to load room')
  }
})

// ------------------------------------------------
// React Frontend
// ------------------------------------------------

// In production the same Fly Machine serves the React build and the API.
const webDistDir = path.join(whiteboardDir, 'dist')

if (existsSync(path.join(webDistDir, 'index.html'))) {
  await app.register(staticPlugin, {
    root: webDistDir,
    prefix: '/'
  })

  app.get('/b/:boardId', async (_req, reply) => {
    return reply.sendFile('index.html')
  })

} else {
  app.get('/', async () => ({
    ok: true,
    message: 'Start the frontend: cd whiteboard && npm run dev:web'
  }))
}

// ------------------------------------------------
// Graceful Shutdown
// ------------------------------------------------

app.addHook('onClose', async () => {
  rooms.close()
  boards.clear()
})

// ------------------------------------------------
// Start Server
// ------------------------------------------------

try {
  await app.listen({
    port,
    host: process.env.TLDRAW_HOST || '127.0.0.1'
  })
} catch (error) {
  app.log.error(error)
  process.exit(1)
}
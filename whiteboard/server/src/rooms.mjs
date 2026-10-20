import { InMemorySyncStorage, TLSocketRoom } from '@tldraw/sync-core'

const ROOM_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * One live room per board, held in this Node.js process.
 *
 * All document data is ephemeral: it survives client disconnects, but NOT
 * a process restart, Fly Machine replacement, or a new deployment.
 * Run exactly one Node.js instance unless a shared sync layer is added.
 */
export class RoomRegistry {
  constructor() {
    this.rooms = new Map()
  }

  get(boardId) {
    if (!ROOM_ID.test(boardId)) {
      throw new Error('Invalid board ID')
    }

    const existing = this.rooms.get(boardId)
    if (existing && !existing.isClosed()) {
      return existing
    }

    const room = new TLSocketRoom({
      storage: new InMemorySyncStorage(),
    })

    // Keep the room even after the last client disconnects, so a reconnect
    // or /tldraw_analyze can still access the board's latest sticky notes.
    this.rooms.set(boardId, room)
    return room
  }

  close() {
    for (const room of this.rooms.values()) {
      if (!room.isClosed()) {
        room.close()
      }
    }
    this.rooms.clear()
  }
}
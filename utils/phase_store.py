"""Durable phase boundaries, idempotent transitions and participant records."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class PhaseConflict(ValueError):
    pass


class PhaseStore:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS phases (
                guild_id TEXT, channel_id TEXT, experiment_id TEXT, phase_id TEXT,
                phase_name TEXT, sequence INTEGER, effective_at TEXT,
                PRIMARY KEY(guild_id, channel_id)
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL,
                guild_id TEXT, channel_id TEXT, phase_id TEXT,
                UNIQUE(guild_id, channel_id, phase_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY, guild_id TEXT, channel_id TEXT,
                phase_id TEXT, content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_phase ON messages(guild_id, channel_id, phase_id);
        ''')

    def current(self, guild_id, channel_id):
        row = self.db.execute('SELECT * FROM phases WHERE guild_id=? AND channel_id=?',
                              (str(guild_id), str(channel_id))).fetchone()
        return dict(row) if row else None

    def transition(self, event):
        payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
        with self.db:
            saved = self.db.execute('SELECT * FROM events WHERE event_id=?', (event['event_id'],)).fetchone()
            if saved:
                if saved['payload'] != payload:
                    raise PhaseConflict('event_id already used for a different transition')
                return json.loads(saved['result'])
            current = self.current(event['guild_id'], event['channel_id'])
            expected = current['sequence'] + 1 if current else 1
            if current and current['experiment_id'] != event['experiment_id']:
                raise PhaseConflict('channel is already bound to another experiment')
            if event['sequence'] != expected:
                raise PhaseConflict(f'expected sequence {expected}')
            # Reusing a phase ID would merge two distinct phase histories.
            previous = self.db.execute(
                'SELECT 1 FROM events WHERE guild_id=? AND channel_id=? AND phase_id=?',
                (event['guild_id'], event['channel_id'], event['phase_id'])).fetchone()
            if previous:
                raise PhaseConflict('phase_id has already been used in this channel')
            effective_at = datetime.now(timezone.utc).isoformat()
            self.db.execute('INSERT OR REPLACE INTO phases VALUES (?,?,?,?,?,?,?)', (
                event['guild_id'], event['channel_id'], event['experiment_id'],
                event['phase_id'], event['phase_name'], event['sequence'], effective_at))
            result = dict(status='applied', event_id=event['event_id'], channel_id=event['channel_id'],
                          phase_id=event['phase_id'], sequence=event['sequence'], effective_at=effective_at)
            self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                            (event['event_id'], payload, json.dumps(result),
                             event['guild_id'], event['channel_id'], event['phase_id']))
            return result

    def record(self, guild_id, channel_id, record, message_id=None):
        phase = self.current(guild_id, channel_id)
        if not phase:
            return False
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?)', (
                str(message_id or uuid4()), str(guild_id), str(channel_id),
                phase['phase_id'], json.dumps(record, ensure_ascii=False)))
        return True

    def records(self, guild_id, channel_id):
        phase = self.current(guild_id, channel_id)
        if not phase:
            return []
        rows = self.db.execute('SELECT content FROM messages WHERE guild_id=? AND channel_id=? AND phase_id=? ORDER BY rowid',
                               (str(guild_id), str(channel_id), phase['phase_id'])).fetchall()
        return [json.loads(row['content']) for row in rows]

    def close(self):
        self.db.close()

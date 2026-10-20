import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from utils.phase_api import create_control_app
from utils.phase_store import PhaseStore, PhaseConflict


def event(sequence=1, channel='10', **changes):
    data = dict(event_id=f'{channel}-{sequence}', experiment_id='exp', guild_id='1',
                channel_id=channel, phase_id=f'phase-{sequence}', phase_name='階段', sequence=sequence)
    return dict(data, **changes)


class StoreTests(unittest.TestCase):
    def test_persistence_and_idempotent_replay_after_new_phase(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'phases.sqlite3'
            store = PhaseStore(path)
            first = store.transition(event())
            store.record(1, 10, {'content':'old'}, message_id='1')
            store.transition(event(2))
            store.record(1, 10, {'content':'new'}, message_id='2')
            store.record(1, 10, {'content':'duplicate'}, message_id='2')
            store.close()
            restored = PhaseStore(path)
            self.addCleanup(restored.close)
            self.assertEqual(restored.transition(event()), first)
            self.assertEqual(restored.current(1, 10)['sequence'], 2)
            self.assertEqual(restored.records(1, 10), [{'content':'new'}])

    def test_conflicting_events_do_not_change_phase(self):
        store = PhaseStore(':memory:')
        self.addCleanup(store.close)
        store.transition(event())
        for bad in [event(1, event_id='other'), event(2, experiment_id='another'),
                    event(2, phase_id='phase-1'), event(3), event(1, phase_name='changed')]:
            with self.assertRaises(PhaseConflict):
                store.transition(bad)
            self.assertEqual(store.current(1, 10)['sequence'], 1)

    def test_unphased_messages_are_not_included_later(self):
        store = PhaseStore(':memory:')
        self.addCleanup(store.close)
        self.assertFalse(store.record(1, 10, {'content':'before phase'}))
        store.transition(event())
        self.assertEqual(store.records(1, 10), [])


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = PhaseStore(':memory:')
        self.locks = {}
        self.validator = AsyncMock(return_value=True)
        self.token = 'test-only-placeholder-' * 3
        self.client = TestClient(TestServer(create_control_app(
            self.store, self.token, '1', self.lock, self.validator)))
        await self.client.start_server()
        self.headers = {'Authorization':'Bearer '+self.token}

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()

    def lock(self, guild, channel):
        return self.locks.setdefault((guild, channel), asyncio.Lock())

    async def post(self, data, headers=None):
        return await self.client.post('/internal/v1/phase-transitions', json=data,
                                      headers=self.headers if headers is None else headers)

    async def test_auth_guild_and_payload(self):
        self.assertEqual((await self.post(event(), {})).status, 401)
        self.assertEqual((await self.post(event(guild_id='2'))).status, 403)
        for bad in [event(sequence=True), event(channel_id=10), {'phase_name':'only one field'},
                    event(phase_name='x'*121), event(sequence=-1)]:
            self.assertEqual((await self.post(bad)).status, 400)
        self.assertIsNone(self.store.current(1, 10))

    async def test_transition_replay_conflict_and_status(self):
        response = await self.post(event())
        self.assertEqual(response.status, 200)
        first = await response.json()
        self.store.record(1, 10, {'content':'keep'})
        repeated = await self.post(event())
        self.assertEqual(await repeated.json(), first)
        self.assertEqual(self.store.records(1, 10), [{'content':'keep'}])
        self.assertEqual((await self.post(event(event_id='new'))).status, 409)
        status = await self.client.get('/internal/v1/channels/1/10/phase', headers=self.headers)
        self.assertEqual((await status.json())['phase_id'], 'phase-1')
        self.assertEqual((await self.client.get('/internal/v1/channels/1/10/phase')).status, 401)

    async def test_discord_unavailable_does_not_apply(self):
        self.validator.return_value = False
        self.assertEqual((await self.post(event())).status, 503)
        self.assertIsNone(self.store.current(1, 10))

    async def test_local_helper_can_configure_and_start_without_resetting_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            script = root / 'scripts' / 'prepare_summary_demo.py'
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts' / script.name, script)
            (root / '.env').write_text(f'PHASE_SYNC_TOKEN={self.token}\nDISCORD_GUILD_ID=1\nPHASE_CONTROL_PORT={self.client.server.port}\n')
            env = {key: value for key, value in os.environ.items() if key not in ('PHASE_SYNC_TOKEN', 'DISCORD_GUILD_ID', 'PHASE_CONTROL_PORT')}
            async def run(*args):
                process = await asyncio.create_subprocess_exec(sys.executable, str(script), *args,
                    env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertNotIn(self.token, stdout.decode())
            await run('--configure')
            await run('--channel-id', '10')
            before = self.store.current(1, 10)
            self.store.record(1, 10, {'content':'keep'})
            await run('--channel-id', '10')
            self.assertEqual(self.store.current(1, 10), before)
            self.assertEqual(self.store.records(1, 10), [{'content':'keep'}])

    async def test_api_uses_same_channel_lock_but_other_channel_can_progress(self):
        lock = self.lock(1, 10)
        await lock.acquire()
        task = asyncio.create_task(self.post(event()))
        try:
            for _ in range(30):
                if self.validator.await_count:
                    break
                await asyncio.sleep(.01)
            self.assertFalse(task.done())
            self.assertEqual((await self.post(event(channel='20'))).status, 200)
            self.assertIsNone(self.store.current(1, 10))
        finally:
            lock.release()
        self.assertEqual((await task).status, 200)

"""Offline Discord handler tests; no real tokens or network calls required."""
import asyncio
import json
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch, Mock
from utils.phase_store import PhaseStore

# Keep application import independent of API credentials.
llm_module = ModuleType('utils.gemini_utils')
llm_module.llm = SimpleNamespace(ainvoke=AsyncMock())
with patch.dict(sys.modules, {'utils.gemini_utils': llm_module}):
    import app


def message(channel, text, user='participant', guild=1):
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild), channel=SimpleNamespace(id=channel),
        content=text, author=SimpleNamespace(bot=False, name=user),
        reply=AsyncMock(),
    )


def interaction(channel):
    return SimpleNamespace(guild_id=1, channel_id=channel,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app.channel_states.clear()
        self.store = PhaseStore(':memory:')
        self.addCleanup(self.store.close)
        store_patch = patch.object(app, 'phase_store', self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        for guild, channel in [(1, 10), (1, 20), (2, 10)]:
            self.store.transition(dict(event_id=f'{guild}-{channel}', experiment_id='exp',
                guild_id=str(guild), channel_id=str(channel), phase_id='initial', phase_name='初始階段', sequence=1))
        app.llm.ainvoke.reset_mock()
        self.bot_user = patch.object(app.bot._connection, 'user', SimpleNamespace(id=42))
        self.bot_user.start()
        self.addCleanup(self.bot_user.stop)
        self.commands = patch.object(app.bot, 'process_commands', new=AsyncMock())
        self.commands.start()
        self.addCleanup(self.commands.stop)

    async def test_concurrent_summaries_only_see_own_channel(self):
        async def invoke(messages):
            await asyncio.sleep(0)
            prompt = messages[-1].content
            self.assertFalse('ALPHA' in prompt and 'BETA' in prompt)
            if 'Intent Detection' in prompt:
                return SimpleNamespace(content='{"Intent":"finished"}')
            return SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        app.llm.ainvoke.side_effect = invoke
        await app.on_message(message(10, 'ALPHA'))
        await app.on_message(message(20, 'BETA'))
        a, b = message(10, '<@42> 摘要'), message(20, '<@42> 摘要')
        await asyncio.gather(app.on_message(a), app.on_message(b))
        self.assertEqual(app.llm.ainvoke.await_count, 2)
        a.reply.assert_awaited_once()
        b.reply.assert_awaited_once()
        self.assertEqual(self.store.records(1, 10)[0]['content'], 'ALPHA')
        self.assertEqual(self.store.records(1, 20)[0]['content'], 'BETA')

    async def test_same_channel_serializes_messages_and_indexes(self):
        async def invoke(messages):
            await asyncio.sleep(0)
            return SimpleNamespace(content='{"Intent":"discussion"}')
        app.llm.ainvoke.side_effect = invoke
        await asyncio.gather(*(app.on_message(message(10, str(i))) for i in range(3)))
        self.assertEqual([m['content'] for m in self.store.records(1, 10)], ['0', '1', '2'])
        self.assertEqual(self.store.records(1, 20), [])
        self.assertEqual(self.store.records(2, 10), [])

    async def test_explicit_mention_summarizes_once(self):
        await app.on_message(message(10, '參與者的討論'))
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        m = message(10, '<@!42> 請統整')
        await app.on_message(m)
        app.llm.ainvoke.assert_awaited_once()
        m.reply.assert_awaited_once()

    async def test_pasted_name_triggers_for_both_people(self):
        await app.on_message(message(10, '參與者的討論'))
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        for user, text in [('yuu8753', '@工具代理 摘要'), ('JinYu', '@工具代理')]:
            m = message(10, text, user=user)
            await app.on_message(m)
            m.reply.assert_awaited_once()
        self.assertEqual(app.llm.ainvoke.await_count, 2)

    def test_pasted_name_boundaries(self):
        for text in ['@工具代理 摘要', '@工具代理', '@工具代理，請統整', '請 @工具代理 摘要']:
            self.assertTrue(app._summary_requested(text), text)
        for text in ['@工具代理人 摘要', '工具代理 摘要', '@everyone', '<@999>', '摘要']:
            self.assertFalse(app._summary_requested(text), text)

    async def test_named_role_mention_triggers_summary(self):
        await app.on_message(message(10, '參與者的討論'))
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        m = message(10, '<@&1234567890123456789> 摘要')
        m.role_mentions = [SimpleNamespace(id=1234567890123456789, name='工具代理')]
        await app.on_message(m)
        m.reply.assert_awaited_once()
        app.llm.ainvoke.assert_awaited_once()

    def test_other_role_does_not_trigger(self):
        roles = [SimpleNamespace(id=123, name='其他角色')]
        self.assertFalse(app._summary_requested('<@&123> 摘要', roles))
        self.assertFalse(app._summary_requested('<@&456> 摘要', [SimpleNamespace(id=123, name='工具代理')]))

    async def test_ordinary_messages_never_call_ai_or_reply(self):
        app.llm.ainvoke.side_effect = RuntimeError('must not call AI')
        for text in ['一般討論', '討論結束，請總結', '@everyone', '<@999> 請統整']:
            m = message(10, text)
            await app.on_message(m)
            m.reply.assert_not_awaited()
        app.llm.ainvoke.assert_not_awaited()
        self.assertEqual(len(self.store.records(1, 10)), 4)

    async def test_different_people_can_both_request_in_same_channel(self):
        await app.on_message(message(10, '參與者的討論'))
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        first = message(10, '<@42> 摘要', user='yuu8753')
        second = message(10, '<@42> 摘要', user='JinYu')
        await app.on_message(first)
        await app.on_message(second)
        first.reply.assert_awaited_once()
        second.reply.assert_awaited_once()
        self.assertEqual(app.llm.ainvoke.await_count, 2)

    async def test_allowed_bot_requests_only_own_channel_without_being_recorded(self):
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        self.store.record(1, 10, {'user':'human', 'content':'ALPHA'})
        self.store.record(1, 20, {'user':'human', 'content':'BETA'})
        m = message(10, '<@42> 摘要')
        m.author = SimpleNamespace(id=1560949145030893618, bot=True)
        await app.on_message(m)
        m.reply.assert_awaited_once()
        prompt = app.llm.ainvoke.call_args.args[0][-1].content
        self.assertIn('ALPHA', prompt)
        self.assertNotIn('BETA', prompt)
        self.assertEqual(len(self.store.records(1, 10)), 1)
        app.bot.process_commands.assert_not_awaited()

    async def test_bots_cannot_trigger_loop_or_record_ordinary_messages(self):
        for bot_id, text in [(42, '<@42> 摘要'), (999, '<@42> 摘要'), (1560949145030893618, '引導問題'), (1560949145030893618, '!ping')]:
            m = message(10, text)
            m.author = SimpleNamespace(id=bot_id, bot=True)
            await app.on_message(m)
            m.reply.assert_not_awaited()
        app.llm.ainvoke.assert_not_awaited()
        self.assertEqual(app.channel_states, {})

    async def test_allowed_bot_empty_channel_gets_no_history_notice(self):
        m = message(10, '<@42> 摘要')
        m.author = SimpleNamespace(id=1560949145030893618, bot=True)
        await app.on_message(m)
        m.reply.assert_awaited_once()
        app.llm.ainvoke.assert_not_awaited()

    async def test_boards_bound_to_creation_channel_and_default_latest(self):
        board_id = '12345678-1234-1234-1234-123456789012'
        client = SimpleNamespace(create_board=AsyncMock(return_value={
            'id': board_id, 'url': 'https://example.com/b/'+board_id}),
            get_sticky_notes=AsyncMock(return_value=[{'id':'note', 'content':'idea'}]),
            board_url=lambda _: 'https://example.com/b/'+board_id)
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        factory = Mock(return_value=manager)
        factory.parse_board_id = app.TldrawClient.parse_board_id
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='白板摘要')
        with patch.object(app, 'TldrawClient', factory):
            await app.create_tldraw_board.callback(interaction(10), '第一組')
            await app.analyze_tldraw_board.callback(interaction(10))
            client.get_sticky_notes.assert_awaited_once_with(board_id)
            client.get_sticky_notes.reset_mock()
            other = interaction(20)
            await app.analyze_tldraw_board.callback(other, board_id)
            client.get_sticky_notes.assert_not_awaited()
            self.assertIn('本頻道', other.followup.send.call_args.args[0])
        self.assertEqual(app.channel_state(1, 20).boards, [])

    async def test_empty_channel_does_not_reuse_other_board(self):
        app.channel_state(1, 10).boards.append('12345678-1234-1234-1234-123456789012')
        with patch.object(app, 'TldrawClient') as client:
            await app.analyze_tldraw_board.callback(interaction(20))
            client.assert_not_called()

    async def test_stage_request_separation_and_repeat_summary(self):
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"本階段觀點"}')
        await app.on_message(message(10, '上一階段內容'))
        await app.on_message(message(20, '另一頻道內容'))
        self.store.transition(dict(event_id='next', experiment_id='exp', guild_id='1', channel_id='10', phase_id='converge', phase_name='收斂', sequence=2))
        await app.on_message(message(10, '新階段的方向與理由'))
        for instruction in ['請保留分歧', '只整理選擇與理由']:
            m = message(10, '＠工具代理 ' + instruction)
            await app.on_message(m)
            call = app.llm.ainvoke.call_args.args[0]
            data = json.loads(call[-1].content)
            self.assertEqual(data['request'], instruction)
            self.assertEqual(data['phase']['phase_name'], '收斂')
            self.assertEqual([item['content'] for item in data['conversation_records']], ['新階段的方向與理由'])
            embed = m.reply.call_args.kwargs['embed']
            self.assertEqual(embed.description, '本階段觀點')
            self.assertEqual(embed.fields, [])
        self.assertEqual(len(self.store.records(1, 10)), 1)
        self.assertEqual(self.store.current(1, 20)['sequence'], 1)

    async def test_empty_stage_does_not_summarize_old_messages(self):
        await app.on_message(message(10, '舊的內容'))
        self.store.transition(dict(event_id='next', experiment_id='exp', guild_id='1', channel_id='10', phase_id='decide', phase_name='決策', sequence=2))
        m = message(10, '@工具代理 摘要')
        await app.on_message(m)
        app.llm.ainvoke.assert_not_awaited()
        self.assertIn('本階段尚無', m.reply.call_args.args[0])

    async def test_invalid_stage_command_preserves_stage(self):
        await app.on_message(message(10, '討論內容'))
        await app.on_message(message(10, '@工具代理 開始階段'))
        self.assertEqual(self.store.current(1, 10)['sequence'], 1)
        self.assertEqual(self.store.current(1, 10)['phase_name'], '初始階段')

    async def test_channel_without_phase_never_falls_back_to_whole_channel(self):
        await app.on_message(message(30, '階段之前的討論'))
        m = message(30, '@工具代理 摘要')
        await app.on_message(m)
        app.llm.ainvoke.assert_not_awaited()
        self.assertIn('尚未開始階段', m.reply.call_args.args[0])
        self.assertEqual(self.store.records(1, 30), [])

    async def test_long_summary_attached_without_discord_field_overflow(self):
        await app.on_message(message(10, '長篇討論'))
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content=json.dumps({'Title':'摘要', 'Summary':'字' * 5000}))
        m = message(10, '@工具代理 摘要')
        await app.on_message(m)
        kwargs = m.reply.call_args.kwargs
        self.assertLessEqual(len(kwargs['embed'].description), 4096)
        self.assertIn('file', kwargs)
        self.assertEqual(kwargs['file'].fp.getvalue().decode(), '字' * 5000)


if __name__ == '__main__':
    unittest.main()

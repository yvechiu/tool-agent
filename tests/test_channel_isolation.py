"""Offline Discord handler tests; no real tokens or network calls required."""
import asyncio
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch, Mock

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
            prompt = messages[0].content
            self.assertFalse('ALPHA' in prompt and 'BETA' in prompt)
            if 'Intent Detection' in prompt:
                return SimpleNamespace(content='{"Intent":"finished"}')
            return SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        app.llm.ainvoke.side_effect = invoke
        a, b = message(10, 'ALPHA <@42>'), message(20, 'BETA <@42>')
        await asyncio.gather(app.on_message(a), app.on_message(b))
        self.assertEqual(app.llm.ainvoke.await_count, 2)
        a.reply.assert_awaited_once()
        b.reply.assert_awaited_once()
        self.assertEqual(app.channel_state(1, 10).messages[0]['content'], 'ALPHA <@42>')
        self.assertEqual(app.channel_state(1, 20).messages[0]['content'], 'BETA <@42>')

    async def test_same_channel_serializes_messages_and_indexes(self):
        async def invoke(messages):
            await asyncio.sleep(0)
            return SimpleNamespace(content='{"Intent":"discussion"}')
        app.llm.ainvoke.side_effect = invoke
        await asyncio.gather(*(app.on_message(message(10, str(i))) for i in range(3)))
        self.assertEqual([m['index'] for m in app.channel_state(1, 10).messages], [0, 1, 2])
        self.assertEqual(app.channel_state(1, 20).messages, [])
        self.assertEqual(app.channel_state(2, 10).messages, [])

    async def test_explicit_mention_summarizes_once(self):
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        m = message(10, '<@!42> 請統整')
        await app.on_message(m)
        app.llm.ainvoke.assert_awaited_once()
        m.reply.assert_awaited_once()

    async def test_ordinary_messages_never_call_ai_or_reply(self):
        app.llm.ainvoke.side_effect = RuntimeError('must not call AI')
        for text in ['一般討論', '討論結束，請總結', '@everyone', '<@999> 請統整']:
            m = message(10, text)
            await app.on_message(m)
            m.reply.assert_not_awaited()
        app.llm.ainvoke.assert_not_awaited()
        self.assertEqual(len(app.channel_state(1, 10).messages), 4)

    async def test_different_people_can_both_request_in_same_channel(self):
        app.llm.ainvoke.side_effect = None
        app.llm.ainvoke.return_value = SimpleNamespace(content='{"Title":"摘要","Summary":"內容","Participation":"參與"}')
        first = message(10, '<@42> 摘要', user='yuu8753')
        second = message(10, '<@42> 摘要', user='JinYu')
        await app.on_message(first)
        await app.on_message(second)
        first.reply.assert_awaited_once()
        second.reply.assert_awaited_once()
        self.assertEqual(app.llm.ainvoke.await_count, 2)

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

    async def test_participation_only_counts_current_channel(self):
        app.channel_state(1, 20).messages.extend([{'user':'other'}] * 20)
        state = app.channel_state(1, 10)
        state.messages.extend([{'user':'active'}] * 6 + [{'user':'quiet'}])
        result = app._message_frequency(state.messages)
        self.assertEqual(result, {'active':6, 'quiet':1, 'quiet_user':['quiet']})


if __name__ == '__main__':
    unittest.main()

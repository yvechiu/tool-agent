import re
import os
import json
import pytz
import logging
import asyncio
import io
from typing import Any
from datetime import datetime
from dataclasses import dataclass, field

import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web
from utils.phase_store import PhaseStore
from utils.phase_api import create_control_app

from langchain_core.messages import SystemMessage, HumanMessage
from utils.gemini_utils import llm
from utils.tldraw_client import TldrawClient
import httpx
from utils.prompts import get_summary_agent_prompt, get_analysis_agent_prompt

from dotenv import load_dotenv
load_dotenv(".env")

# =============================================
# logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# timezone
taiwan_tz = pytz.timezone("Asia/Taipei")
current_time_taiwan = datetime.now(taiwan_tz)
version = current_time_taiwan.strftime("v%Y-%m%d-%H%M")

# discord chatbot setup
intents = discord.Intents.default()
intents.message_content = True
phase_store: PhaseStore | None = None


class ToolAgentBot(commands.Bot):
    phase_runner = None

    async def close(self):
        try:
            if self.phase_runner is not None:
                await self.phase_runner.cleanup()
                self.phase_runner = None
            await super().close()
        finally:
            global phase_store
            if phase_store is not None:
                phase_store.close()
                phase_store = None


bot = ToolAgentBot(command_prefix="!", intents=intents)

# State is keyed by immutable guild/channel IDs, including thread IDs.
@dataclass
class ChannelState:
    boards: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


channel_states: dict[tuple[int, int], ChannelState] = {}


def channel_state(guild_id: int, channel_id: int) -> ChannelState:
    return channel_states.setdefault((guild_id, channel_id), ChannelState())


# =============================================
# Helper Function

def _safe_parse_json(text: str) -> dict:
    if isinstance(text, dict):
        return text
    if not text or not isinstance(text, str):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        cleaned = re.sub(r"```json|```", "", text).strip()
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                parsed = json.loads(m.group())
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError as e:
                logger.warning("Summary JSON could not be decoded")
                return {}
        else:
            logger.warning("Summary response contained no JSON object")
            return {}

def _stringify(obj: Any) -> str:
    """Pretty-print dict / list, otherwise str()."""
    if isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return str(obj)

# =============================================
# Main Entry Point

async def start_phase_control():
    global phase_store
    phase_store = PhaseStore(os.getenv("PHASE_DB_PATH", "data/phases.sqlite3"))
    token = os.getenv("PHASE_SYNC_TOKEN", "").strip()
    if not token:
        logger.warning("Phase API disabled: set PHASE_SYNC_TOKEN to start phase synchronization; summaries need an active phase")
        return

    async def validate_channel(guild_id, channel_id):
        if not bot.is_ready():
            return False
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return False
        guild = getattr(channel, "guild", None)
        if guild is None or guild.id != int(guild_id):
            return False
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False
        member = guild.me
        return bool(member and channel.permissions_for(member).view_channel)

    control = create_control_app(
        phase_store, token, os.getenv("DISCORD_GUILD_ID", "").strip(),
        lambda guild, channel: channel_state(guild, channel).lock, validate_channel,
    )
    runner = web.AppRunner(control, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, os.getenv("PHASE_CONTROL_HOST", "127.0.0.1"),
                          int(os.getenv("PHASE_CONTROL_PORT", "5860"))).start()
    except Exception:
        await runner.cleanup()
        raise
    bot.phase_runner = runner
    logger.info("Phase control API started on port %s", os.getenv("PHASE_CONTROL_PORT", "5860"))


@bot.event
async def setup_hook():
    """
    Register slash commands before the Discord gateway is ready.

    During development, setting DISCORD_GUILD_ID makes Discord update the
    commands for one server immediately. Without it, commands are registered
    globally and Discord may take a while to refresh its command cache.
    """
    await start_phase_control()
    try:
        guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
        # Always sync global commands too. This removes commands that were
        # registered globally in an earlier version of the bot.
        synced_global = await bot.tree.sync()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            scope = f"global + guild {guild_id}"
        else:
            synced = synced_global
            scope = "global"

        command_names = ", ".join(command.name for command in synced)
        logger.info(
            "Synced %d %s slash commands: %s",
            len(synced),
            scope,
            command_names or "(none)",
        )
    except ValueError:
        logger.error(
            "Invalid DISCORD_GUILD_ID=%r. Copy the numeric server ID from Discord.",
            os.getenv("DISCORD_GUILD_ID", "").strip(),
        )
    except discord.Forbidden:
        logger.error(
            "Discord denied guild command sync for DISCORD_GUILD_ID=%s. "
            "Check that this is the server ID and that this bot is a member of that server.",
            os.getenv("DISCORD_GUILD_ID", "").strip(),
        )
    except discord.HTTPException:
        logger.exception("Could not sync slash commands")


@bot.event
async def on_ready():
    """
    Check Discord Bot Connection
    """
    logger.info(f"✅ Bot 登入成功: {bot.user.name} (ID: {bot.user.id})")
    logger.info("--------------------------------")

def _summary_request_text(content: str, role_mentions=()) -> str | None:
    """Return instructions after the first supported mention, or None."""
    content = content.replace("＠", "@")
    patterns = [r"(?<!\w)@工具代理(?=$|[\s，。！？、:：,!?])"]
    if bot.user:
        patterns.append(rf"<@!?{bot.user.id}>")
    patterns.extend(
        re.escape(f"<@&{role.id}>")
        for role in role_mentions if role.name == "工具代理"
    )
    matches = [match for pattern in patterns if (match := re.search(pattern, content))]
    if not matches:
        return None
    match = min(matches, key=lambda item: item.start())
    return content[match.end():].strip().lstrip("，,:：").strip()


def _summary_requested(content: str, role_mentions=()) -> bool:
    return _summary_request_text(content, role_mentions) is not None


ALLOWED_SUMMARY_BOT_IDS = {1560949145030893618}


@bot.event
async def on_message(message: discord.Message):
    """
    Monitor Conversation 
    """

    logger.info(
        "Message received: channel=%s author=%s bot=%s chars=%s explicit_mention=%s summary_trigger=%s",
        message.channel.id, getattr(message.author, "id", "unknown"),
        message.author.bot, len(message.content),
        bool(bot.user and re.search(rf"<@!?{bot.user.id}>", message.content)),
        _summary_requested(message.content, getattr(message, "role_mentions", ())),
    )

    # Never respond to ourselves, including a summary containing a mention.
    if bot.user and getattr(message.author, "id", None) == bot.user.id:
        return
    if message.author.bot:
        if getattr(message.author, "id", None) not in ALLOWED_SUMMARY_BOT_IDS:
            return
        if not _summary_requested(message.content, getattr(message, "role_mentions", ())):
            return
    elif message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # Direct messages are outside the experiment workflow.
    if message.guild is None:
        return
    state = channel_state(message.guild.id, message.channel.id)
    # Serialize only this channel; other experiments can progress concurrently.
    if state.lock.locked():
        logger.info("Message queued: channel=%s", message.channel.id)
    async with state.lock:
        try:
            await _handle_channel_message(message, state)
        except discord.HTTPException as exc:
            logger.warning(
                "Discord response failed: channel=%s author=%s status=%s code=%s",
                message.channel.id, getattr(message.author, "id", "unknown"), exc.status, exc.code,
            )
        except (httpx.RequestError, RuntimeError, ValueError) as exc:
            logger.warning("AI processing failed in channel %s (%s)", message.channel.id, type(exc).__name__)
            await message.reply("AI 處理失敗，請檢查 GEMINI_API_KEY、模型、配額及網路連線後再試。")


async def _handle_channel_message(message: discord.Message, state: ChannelState):
    instructions = _summary_request_text(message.content, getattr(message, "role_mentions", ()))
    if phase_store is None:
        if instructions is not None:
            await message.reply("階段服務尚未就緒，請聯絡實驗主持者。")
        return
    if instructions is None:
        if not message.author.bot:
            phase_store.record(message.guild.id, message.channel.id, {
                "content": message.content,
                "timestamp": message.created_at.isoformat() if hasattr(message, "created_at") else datetime.now(taiwan_tz).isoformat(),
                "user": str(message.author),
            }, getattr(message, "id", None))
        return

    # Phase transitions are accepted only through the authenticated backend API.
    if instructions == "開始階段" or instructions.startswith("開始階段 "):
        await message.reply("階段只能由 AI引導者的系統切換；聊天訊息不會切換階段。")
        return
    phase = phase_store.current(message.guild.id, message.channel.id)
    if phase is None:
        await message.reply("尚未開始階段，請實驗主持者先在 AI引導者系統開始本階段。")
        return
    messages_history = phase_store.records(message.guild.id, message.channel.id)
    if not messages_history:
        await message.reply("本階段尚無可摘要的參與者對話，請先開始討論。")
        return

    if instructions in ("", "摘要", "統整", "總結"):
        instructions = "整理本階段主要觀點、明確共識、不同意見與待釐清事項。"
    logger.info("Summary started: channel=%s author=%s messages=%s",
                message.channel.id, getattr(message.author, "id", "unknown"), len(messages_history))
    summary_response = await llm.ainvoke([
        SystemMessage(content=get_summary_agent_prompt()),
        HumanMessage(content=json.dumps({
            "phase": {key: phase[key] for key in ("phase_id", "phase_name", "experiment_id", "sequence")},
            "request": instructions,
            "conversation_records": messages_history,
        }, ensure_ascii=False)),
    ])
    result = _safe_parse_json(summary_response.content)
    title, summary = result.get("Title"), result.get("Summary")
    if not isinstance(title, str) or not isinstance(summary, str) or not summary.strip():
        raise ValueError("Invalid summary JSON")
    # Discord embeds allow 4096 description characters; attach full long reports.
    embed = discord.Embed(title=(title or "本階段摘要")[:256],
                          description=summary[:3800], color=0x5865F2)
    embed.set_footer(text=f"階段：{phase['phase_name']}"[:2048])
    kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
    if len(summary) > 3800:
        kwargs["file"] = discord.File(io.BytesIO(summary.encode("utf-8")), filename="phase_summary.md")
    await message.reply(**kwargs)
    logger.info("Summary sent: channel=%s author=%s",
                message.channel.id, getattr(message.author, "id", "unknown"))

# health check
@bot.command()
async def ping(ctx):
    await ctx.send("Pong! 🏓 WebSocket 連線正常！")


# =============================== tldraw slash command ===============================

def _notes_for_analysis(notes: list[dict], max_chars: int = 24000, max_notes: int = 120):
    nonempty = [note for note in notes if (note.get("content") or "").strip()]
    selected, chars = [], 0
    for note in nonempty:
        item = {"id": note.get("id"), "content": note["content"]}
        encoded = json.dumps(item, ensure_ascii=False)
        if len(selected) >= max_notes or chars + len(encoded) > max_chars:
            break
        selected.append(item)
        chars += len(encoded)
    return selected, len(nonempty)


def _tldraw_error_text(error: httpx.HTTPStatusError) -> str:
    status = error.response.status_code
    descriptions = {
        400: "Invalid whiteboard request or title.",
        401: "Whiteboard backend API key is missing or incorrect.",
        403: "Whiteboard backend denied the request.",
        404: "Whiteboard not found. Check its URL / board ID.",
        429: "Whiteboard backend is rate-limiting requests.",
        500: "Whiteboard backend encountered a server error.",
    }
    return descriptions.get(status, f"Whiteboard backend returned HTTP {status}.")


@bot.tree.command(name="create-board", description="Create a collaborative whiteboard")
@app_commands.describe(title="Title of the new whiteboard (1-60 characters)")
async def create_tldraw_board(interaction: discord.Interaction, title: str):
    if interaction.guild_id is None or interaction.channel_id is None:
        await interaction.response.send_message("請在伺服器頻道內使用白板指令。", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        async with TldrawClient() as client:
            board = await client.create_board(title)

        board_id = TldrawClient.parse_board_id(board["id"])
        board_url = board["url"]
        state = channel_state(interaction.guild_id, interaction.channel_id)
        state.boards.append(board_id)
        embed = discord.Embed(
            title="tldraw Whiteboard Created",
            description=f"[Open and edit the whiteboard]({board_url})",
            color=0x5865F2,
        )
        embed.add_field(name="Title", value=board.get("title") or title, inline=False)
        embed.add_field(name="Board ID", value=f"`{board_id}`", inline=False)
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("tldraw create HTTP %d", exc.response.status_code)
        await interaction.followup.send(_tldraw_error_text(exc), ephemeral=True)
    except httpx.RequestError:
        logger.exception("tldraw backend is unreachable")
        await interaction.followup.send(
            "Cannot reach the whiteboard server. Start `npm run dev:server` first.", ephemeral=True
        )
    except (ValueError, KeyError) as exc:
        await interaction.followup.send(f"Invalid configuration or response: {exc}", ephemeral=True)
    except Exception:
        logger.exception("Unexpected error in /tldraw")
        await interaction.followup.send("Unable to create whiteboard.", ephemeral=True)


@bot.tree.command(name="analyze-board", description="Analyze sticky notes from the whiteboard")
@app_commands.describe(
    board="本頻道建立的白板 URL 或 UUID；留空使用本頻道最新白板",
    private="Send analysis only to you (default: False)",
)
async def analyze_tldraw_board(
    interaction: discord.Interaction,
    board: str | None = None,
    private: bool = False,
):
    if interaction.guild_id is None or interaction.channel_id is None:
        await interaction.response.send_message("請在伺服器頻道內使用白板指令。", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=private)
    try:
        state = channel_state(interaction.guild_id, interaction.channel_id)
        board_id = TldrawClient.parse_board_id(board) if board else (state.boards[-1] if state.boards else None)
        if board_id is None or board_id not in state.boards:
            await interaction.followup.send(
                "請先在本頻道使用 /create-board 建立白板；只能分析本頻道建立的白板。",
                ephemeral=True,
            )
            return
        async with TldrawClient() as client:
            notes = await client.get_sticky_notes(board_id)
            board_url = client.board_url(board_id)

        selected, nonempty_count = _notes_for_analysis(notes)
        if not selected:
            await interaction.followup.send(
                f"Found {len(notes)} sticky notes, but none has text to analyze.",
                ephemeral=private,
            )
            return
        
        tldraw_analysis_prompt = get_analysis_agent_prompt()
        result = await llm.ainvoke([
            SystemMessage(content=tldraw_analysis_prompt),
            HumanMessage(content="Untrusted sticky-note JSON:\n" + json.dumps(selected, ensure_ascii=False)),
        ])
        analysis = result.content.strip()
        embed = discord.Embed(
            title="Whiteboard Analysis", description=analysis, color=0x5865F2
        )
        embed.add_field(name="Whiteboard", value=f"[Open whiteboard]({board_url})", inline=False)
        total = f"Found {len(notes)} notes; analyzed {len(selected)}"
        if len(selected) < nonempty_count:
            total += f" of {nonempty_count} nonempty notes (context cap reached)"
        embed.set_footer(text=total)
        send_kwargs = {
            "embed": embed,
            "ephemeral": private,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if len(analysis) > 3800:
            send_kwargs["file"] = discord.File(
                io.BytesIO(analysis.encode("utf-8")), filename="tldraw_analysis.md"
            )
        await interaction.followup.send(**send_kwargs)
    except httpx.HTTPStatusError as exc:
        logger.warning("tldraw analyze HTTP %d", exc.response.status_code)
        await interaction.followup.send(_tldraw_error_text(exc), ephemeral=private)
    except httpx.RequestError:
        logger.exception("tldraw backend is unreachable")
        await interaction.followup.send("Cannot reach the whiteboard server.", ephemeral=private)
    except (ValueError, KeyError) as exc:
        await interaction.followup.send(f"Invalid board or response: {exc}", ephemeral=private)
    except Exception:
        logger.exception("Unexpected error in /tldraw_analyze")
        await interaction.followup.send("Unable to analyze this whiteboard.", ephemeral=private)


if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_APPLICATION_TOKEN"))

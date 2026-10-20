import re
import os
import json
import pytz
import logging
import asyncio
import io
from typing import Any, List, Dict
from datetime import datetime
from collections import Counter

import discord
from discord.ext import commands
from discord import app_commands

from langchain_core.messages import SystemMessage, HumanMessage
# from langchain_openai import ChatOpenAI
from utils.openai_utils import llm
from utils.tldraw_client import TldrawClient
import httpx
from utils.prompts import get_intent_agent_prompt, get_summary_agent_prompt, get_analysis_agent_prompt

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
bot = commands.Bot(command_prefix="!", intents=intents)

# Message Records
global frequency, phase
frequency, phase = 0, 0

messages_history = []

# =============================================
# Helper Function

def _safe_parse_json(text: str) -> dict:
    if isinstance(text, dict):
        return text
    if not text or not isinstance(text, str):
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r"```json|```", "", text).strip()
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError as e:
                print(f"fallback decode error: {e}\nOrigin Content: {text}")
                return {}
        else:
            print(f"無法找到 json object, Origin Content: {text}")
            return {}

def _stringify(obj: Any) -> str:
    """Pretty-print dict / list, otherwise str()."""
    if isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return str(obj)

def _message_frequency(messages_history: List[Dict]) -> Dict[str, Any]:
    """
    count the frequency of messages sent by each user
    based on a dynamic threshold: 1 / (N + 1), where N is the total number of unique users.
    output: {
        "user name 1": frequency 1,
        "user name 2": frequency 2,
        "quiet_user": ["user name"]  which frequency percentage < 0.15
    }
    """

    message_frequency = Counter(msg["user"] for msg in messages_history if "user" in msg)
    num_users = len(message_frequency)
    result: Dict[str, Any] = dict(message_frequency)

    frequency_threshold = 2 * num_users + 1

    total_frequency = sum(message_frequency.values())
    if total_frequency > frequency_threshold:

        dynamic_threshold = 1.0 / (num_users + 1)

        quiet_users = [
            user for user, count in message_frequency.items()
            if (count / total_frequency) < dynamic_threshold
        ]
    else:
        quiet_users = []

    result["quiet_user"] = quiet_users
    return result
    

# =============================================
# Main Entry Point

@bot.event
async def setup_hook():
    """
    Register slash commands before the Discord gateway is ready.

    During development, setting DISCORD_GUILD_ID makes Discord update the
    commands for one server immediately. Without it, commands are registered
    globally and Discord may take a while to refresh its command cache.
    """
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

@bot.event
async def on_message(message: discord.Message):
    """
    Monitor Conversation 
    """

    if message.author.bot:
        """
        message.author.bot == True >>> message from chatbot
        - need to ignore message from chatbot to avoid infinite loop
        """
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    global frequency

    mes = {
        "index": frequency,
        "content": message.content,
        "timestamp": datetime.now(taiwan_tz).strftime("%Y-%m-%d %H:%M:%S"),
        "user": str(message.author),
    }
    logger.info(f"收到訊息: \n{mes}")
    messages_history.append(mes)

    # check user intent
    intent_system_prompt = get_intent_agent_prompt(origin_query=messages_history)
    intent_response = await llm.ainvoke([SystemMessage(content=intent_system_prompt)])
    json_intent_response = _safe_parse_json(intent_response.content)
    intent = json_intent_response.get("Intent", False)
    logger.info(f"Index: {frequency},\n Intent: {intent}")

    if intent == "discussion":
        frequency += 1

        str_message_history = _stringify(messages_history)

        message_frequency = _message_frequency(messages_history)
        str_message_frequency = _stringify(message_frequency)

        if message_frequency["quiet_user"] != []:
            summary_system_prompt = get_summary_agent_prompt(message_frequency = str_message_frequency, message_history = str_message_history)            
            summary_response = await llm.ainvoke([SystemMessage(content=summary_system_prompt)])
            json_summary_response = _safe_parse_json(summary_response.content)

            participation = json_summary_response.get("Participation", "無法取得參與度評估")

            embed = discord.Embed(
                title="👥 成員參與狀況分析",
                description=participation,
                color=0x5865F2
            )

            await message.reply(embed=embed)

        return

    if bot.user.mentioned_in(message) or intent == "finished" :
        """
        Summary Phase 
        - @CPSAgent
        - intent == "finished" --> user has a summary intent
        """
            
        logger.info(f"Historical messages: {messages_history}")
        str_message_history = _stringify(messages_history)

        message_frequency = _message_frequency(messages_history)
        str_message_frequency = _stringify(message_frequency)
        logger.info(f"User message frequency: {str_message_frequency}")
        
        summary_system_prompt = get_summary_agent_prompt(message_frequency = str_message_frequency, message_history = str_message_history)            
        summary_response = await llm.ainvoke([SystemMessage(content=summary_system_prompt)])
        json_summary_response = _safe_parse_json(summary_response.content)

        title = json_summary_response.get("Title", "無法取得標題")
        summary =json_summary_response.get("Summary", "無法取得內容")
        participation = json_summary_response.get("Participation", "無法取得參與度評估")

        # discord embed message
        embed = discord.Embed(
            title=title,
            color=0x5865F2
        )
        embed.add_field(
            name="📝 會議摘要",
            value=summary,
            inline=False
        )
        embed.add_field(
            name="👥 成員參與狀況分析",
            value=participation,
            inline=False
        )

        await message.reply(embed=embed)
    
    await bot.process_commands(message)

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
    await interaction.response.defer(thinking=True)
    try:
        async with TldrawClient() as client:
            board = await client.create_board(title)

        board_id = TldrawClient.parse_board_id(board["id"])
        board_url = board["url"]
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
    board="tldraw board URL or board UUID",
    private="Send analysis only to you (default: True)",
)
async def analyze_tldraw_board(
    interaction: discord.Interaction,
    board: str,
    private: bool = True,
):
    await interaction.response.defer(thinking=True, ephemeral=private)
    try:
        async with TldrawClient() as client:
            board_id = client.parse_board_id(board)
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

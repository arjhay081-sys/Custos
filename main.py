import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
import re
import logging
import asyncio
import hashlib
from keep_alive import keep_alive

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DATABASE_CHANNEL_ID = 1405783830555529389

class Colors:
    PRIMARY = 0x5865F2
    SUCCESS = 0x57F287
    WARNING = 0xFEE75C
    DANGER = 0xED4245
    INFO = 0x5865F2
    MUTE = 0xEB459E

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

warnings = {}
punishments = {}
server_settings = {}
pending_database_save = False
last_database_save = datetime.now()

def get_user_warnings(sid: int, uid: int) -> List[Dict]:
    if sid not in warnings:
        warnings[sid] = {}
    if uid not in warnings[sid]:
        warnings[sid][uid] = []
    return warnings[sid][uid]

def add_warning(sid: int, uid: int, data: Dict):
    if sid not in warnings:
        warnings[sid] = {}
    if uid not in warnings[sid]:
        warnings[sid][uid] = []
    warnings[sid][uid].append(data)

def add_punishment(sid: int, data: Dict):
    if sid not in punishments:
        punishments[sid] = []
    punishments[sid].append(data)

def get_warning_level_emoji(count: int) -> str:
    if count == 0: return "🟢"
    elif count == 1: return "🟡"
    elif count == 2: return "🟠"
    else: return "🔴"

def format_duration_friendly(dur: str) -> str:
    if dur == "permanent": return "Permanent"
    d_lower = dur.lower()
    result = []
    days = re.search(r'(\d+)d', d_lower)
    hours = re.search(r'(\d+)h', d_lower)
    mins = re.search(r'(\d+)m', d_lower)
    if days:
        d = int(days.group(1))
        result.append(f"{d} day{'s' if d > 1 else ''}")
    if hours:
        h = int(hours.group(1))
        result.append(f"{h} hour{'s' if h > 1 else ''}")
    if mins:
        m = int(mins.group(1))
        result.append(f"{m} minute{'s' if m > 1 else ''}")
    return ", ".join(result) if result else dur

async def batch_save_database():
    global pending_database_save, last_database_save
    if not pending_database_save:
        pending_database_save = True
        await asyncio.sleep(10)
        if (datetime.now() - last_database_save).total_seconds() >= 60:
            await save_database()
            last_database_save = datetime.now()
        pending_database_save = False

async def load_database():
    global warnings, punishments, server_settings
    try:
        db_ch = bot.get_channel(DATABASE_CHANNEL_ID)
        if not db_ch:
            logging.error(f"DB channel {DATABASE_CHANNEL_ID} not found!")
            warnings = {}
            punishments = {}
            server_settings = {}
            return
        warnings = {}
        punishments = {}
        server_settings = {}
        msgs = []
        async for msg in db_ch.history(limit=50):
            if msg.author == bot.user:
                msgs.append(msg)
        msgs.sort(key=lambda m: m.created_at, reverse=True)
        for msg in msgs:
            if not msg.content: continue
            if msg.content.startswith("```json") and \
               msg.content.endswith("```"):
                try:
                    jc = msg.content[7:-3].strip()
                    if not jc: continue
                    data = json.loads(jc)
                    if isinstance(data, dict) and "warnings" in data:
                        wd = data.get("warnings", {})
                        if isinstance(wd, dict):
                            for sid, users in wd.items():
                                try:
                                    sid_int = int(sid)
                                    warnings[sid_int] = {}
                                    if isinstance(users, dict):
                                        for uid, uw in users.items():
                                            try:
                                                uid_int = int(uid)
                                                if isinstance(uw, list):
                                                    warnings[sid_int][uid_int] = uw
                                            except (ValueError, TypeError):
                                                continue
                                except (ValueError, TypeError):
                                    continue
                        pd = data.get("punishments", {})
                        if isinstance(pd, dict):
                            for sid, pl in pd.items():
                                try:
                                    sid_int = int(sid)
                                    if isinstance(pl, list):
                                        punishments[sid_int] = pl
                                except (ValueError, TypeError):
                                    continue
                        sd = data.get("server_settings", {})
                        if isinstance(sd, dict):
                            for sid, sets in sd.items():
                                try:
                                    sid_int = int(sid)
                                    if isinstance(sets, dict):
                                        server_settings[sid_int] = sets
                                except (ValueError, TypeError):
                                    continue
                        logging.info(f"✅ Successfully loaded database:")
                        logging.info(f"   📊 {len(warnings)} servers with warnings")
                        logging.info(f"   🔨 {len(punishments)} servers with punishments")
                        logging.info(f"   ⚙️ {len(server_settings)} servers with settings")
                        return
                except json.JSONDecodeError as e:
                    logging.warning(f"JSON decode error in msg {msg.id}: {e}")
                    continue
                except Exception as e:
                    logging.error(f"Error parsing msg {msg.id}: {e}")
                    continue
        logging.info("No valid database found, starting fresh")
        warnings = {}
        punishments = {}
        server_settings = {}
    except Exception as e:
        logging.error(f"Error loading database: {e}")
        warnings = {}
        punishments = {}
        server_settings = {}

async def save_database():
    try:
        db_ch = bot.get_channel(DATABASE_CHANNEL_ID)
        if not db_ch:
            logging.error(f"DB channel {DATABASE_CHANNEL_ID} not found!")
            return
        warn_str = {str(k): {str(uk): uv for uk, uv in v.items()} 
                    for k, v in warnings.items()}
        pun_str = {str(k): v for k, v in punishments.items()}
        set_str = {str(k): v for k, v in server_settings.items()}
        data = {
            "warnings": warn_str,
            "punishments": pun_str,
            "server_settings": set_str,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        json_str = json.dumps(data, indent=2)
        msg_content = f"```json\n{json_str}\n```"
        if len(msg_content) > 2000:
            logging.error("DB too large for single message!")
            return
        last_msg = None
        async for msg in db_ch.history(limit=1):
            if msg.author == bot.user:
                last_msg = msg
                break
        if last_msg:
            try:
                await asyncio.sleep(1)
                await last_msg.delete()
                await asyncio.sleep(1)
            except discord.HTTPException:
                pass
        await db_ch.send(msg_content)
        logging.info("✅ Database saved successfully")
    except discord.HTTPException as e:
        if e.status == 429:
            logging.warning("⚠️ Rate limited, skipping database save")
        else:
            logging.error(f"HTTP error saving database: {e}")
    except Exception as e:
        logging.error(f"Error saving database: {e}")

async def send_log(guild: discord.Guild, embed: discord.Embed):
    if guild.id not in server_settings:
        return
    settings = server_settings[guild.id]
    if "log_channel_id" not in settings:
        return
    try:
        log_ch = guild.get_channel(settings["log_channel_id"])
        if log_ch and isinstance(log_ch, discord.TextChannel):
            await log_ch.send(embed=embed)
    except discord.HTTPException as e:
        logging.error(f"Error sending log to {guild.name}: {e}")

@tree.command(name="warn", description="Issue a warning to a user")
@app_commands.describe(
    user="User to warn",
    reason="Reason for warning"
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str
):
    await interaction.response.defer(ephemeral=True)
    if user.bot:
        emb = discord.Embed(
            title="❌ Cannot Warn Bot",
            description="You cannot warn a bot user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    if user.id == interaction.user.id:
        emb = discord.Embed(
            title="❌ Cannot Warn Self",
            description="You cannot warn yourself.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    sid = interaction.guild.id
    uid = user.id
    warn_data = {
        "reason": reason,
        "warned_by": interaction.user.id,
        "warned_by_name": str(interaction.user),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    add_warning(sid, uid, warn_data)
    user_warns = get_user_warnings(sid, uid)
    warn_count = len(user_warns)
    emb_mod = discord.Embed(
        title="⚠️ Warning Issued",
        description=f"**{user.mention}** has been warned",
        color=Colors.WARNING,
        timestamp=datetime.now(timezone.utc)
    )
    emb_mod.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    emb_mod.add_field(
        name="👮 Warned By",
        value=interaction.user.mention,
        inline=True
    )
    emb_mod.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    emb_mod.add_field(
        name="📊 Warning Count",
        value=f"{get_warning_level_emoji(warn_count)} **{warn_count}** warning(s)",
        inline=True
    )
    emb_mod.set_footer(
        text="Custos Moderation",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.followup.send(embed=emb_mod, ephemeral=True)
    try:
        emb_dm = discord.Embed(
            title="⚠️ You Have Been Warned",
            description=f"You have received a warning in **{interaction.guild.name}**",
            color=Colors.WARNING,
            timestamp=datetime.now(timezone.utc)
        )
        emb_dm.add_field(
            name="📝 Reason",
            value=f"```{reason}```",
            inline=False
        )
        emb_dm.add_field(
            name="📊 Total Warnings",
            value=f"{get_warning_level_emoji(warn_count)} **{warn_count}** warning(s)",
            inline=True
        )
        emb_dm.add_field(
            name="⚠️ Important",
            value="**3 warnings = automatic ban**",
            inline=True
        )
        emb_dm.set_footer(
            text=f"Warned by {interaction.user} | Custos Moderation",
            icon_url="https://i.imgur.com/AfFp7pu.png"
        )
        await user.send(embed=emb_dm)
    except discord.Forbidden:
        pass
    log_emb = discord.Embed(
        title="⚠️ User Warned",
        description=f"**{user}** received a warning",
        color=Colors.WARNING,
        timestamp=datetime.now(timezone.utc)
    )
    log_emb.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="👮 Warned By",
        value=f"{interaction.user.mention}\n`ID: {interaction.user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    log_emb.add_field(
        name="📊 Total Warnings",
        value=f"{get_warning_level_emoji(warn_count)} **{warn_count}** warning(s)",
        inline=True
    )
    log_emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await send_log(interaction.guild, log_emb)
    asyncio.create_task(batch_save_database())
    if warn_count >= 3:
        try:
            pun_data = {
                "type": "ban",
                "user_id": uid,
                "user_name": str(user),
                "reason": f"Automatic ban: {warn_count} warnings accumulated",
                "punished_by": bot.user.id,
                "punished_by_name": "Custos Auto-Mod",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration": "permanent"
            }
            add_punishment(sid, pun_data)
            await interaction.guild.ban(
                user,
                reason=f"Auto-ban: {warn_count} warnings"
            )
            ban_emb = discord.Embed(
                title="🔨 Automatic Ban",
                description=f"**{user}** banned for accumulating {warn_count} warnings",
                color=Colors.DANGER,
                timestamp=datetime.now(timezone.utc)
            )
            ban_emb.add_field(
                name="👤 User",
                value=f"{user.mention}\n`ID: {user.id}`",
                inline=True
            )
            ban_emb.add_field(
                name="📊 Warnings",
                value=f"🔴 **{warn_count}** warnings",
                inline=True
            )
            ban_emb.set_footer(
                text="Automatic System | Custos Moderation",
                icon_url="https://i.imgur.com/AfFp7pu.png"
            )
            await send_log(interaction.guild, ban_emb)
            asyncio.create_task(batch_save_database())
        except discord.Forbidden:
            err_emb = discord.Embed(
                title="❌ Auto-Ban Failed",
                description="Missing permissions to ban user",
                color=Colors.DANGER
            )
            await interaction.followup.send(embed=err_emb, ephemeral=True)

@tree.command(name="warnings", description="View warnings for a user")
@app_commands.describe(user="User to check warnings for")
async def warnings_cmd(interaction: discord.Interaction, user: discord.Member):
    sid = interaction.guild.id
    uid = user.id
    user_warns = get_user_warnings(sid, uid)
    warn_count = len(user_warns)
    emb = discord.Embed(
        title=f"📋 Warning History for {user}",
        description=f"{get_warning_level_emoji(warn_count)} **{warn_count}** total warning(s)",
        color=Colors.INFO,
        timestamp=datetime.now(timezone.utc)
    )
    emb.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)
    if warn_count == 0:
        emb.add_field(
            name="✅ Clean Record",
            value="This user has no warnings.",
            inline=False
        )
    else:
        for i, warn in enumerate(user_warns[-5:], 1):
            ts = datetime.fromisoformat(warn["timestamp"])
            emb.add_field(
                name=f"⚠️ Warning #{warn_count - len(user_warns) + i}",
                value=f"**Reason:** {warn['reason']}\n**By:** {warn.get('warned_by_name', 'Unknown')}\n**Date:** <t:{int(ts.timestamp())}:R>",
                inline=False
            )
        if warn_count > 5:
            emb.add_field(
                name="ℹ️ Note",
                value=f"Showing last 5 of {warn_count} warnings",
                inline=False
            )
    emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)

@tree.command(name="clearwarnings", description="Clear all warnings for a user")
@app_commands.describe(user="User to clear warnings for")
@app_commands.checks.has_permissions(manage_guild=True)
async def clearwarnings(interaction: discord.Interaction, user: discord.Member):
    sid = interaction.guild.id
    uid = user.id
    if sid not in warnings or uid not in warnings[sid]:
        emb = discord.Embed(
            title="ℹ️ No Warnings",
            description=f"{user.mention} has no warnings to clear.",
            color=Colors.INFO
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)
        return
    old_count = len(warnings[sid][uid])
    warnings[sid][uid] = []
    emb = discord.Embed(
        title="🗑️ Warnings Cleared",
        description=f"Cleared **{old_count}** warning(s) for {user.mention}",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    emb.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    emb.add_field(
        name="👮 Cleared By",
        value=interaction.user.mention,
        inline=True
    )
    emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)
    log_emb = discord.Embed(
        title="🗑️ Warnings Cleared",
        description=f"**{old_count}** warnings cleared for {user}",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    log_emb.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="👮 Cleared By",
        value=f"{interaction.user.mention}\n`ID: {interaction.user.id}`",
        inline=True
    )
    log_emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await send_log(interaction.guild, log_emb)
    asyncio.create_task(batch_save_database())

@tree.command(name="mute", description="Timeout a user")
@app_commands.describe(
    user="User to mute",
    duration="Duration (e.g., 10m, 1h, 1d)",
    reason="Reason for mute"
)
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(
    interaction: discord.Interaction,
    user: discord.Member,
    duration: str,
    reason: str
):
    await interaction.response.defer(ephemeral=True)
    if user.bot:
        emb = discord.Embed(
            title="❌ Cannot Mute Bot",
            description="You cannot mute a bot user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    if user.id == interaction.user.id:
        emb = discord.Embed(
            title="❌ Cannot Mute Self",
            description="You cannot mute yourself.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    dur_lower = duration.lower()
    total_secs = 0
    days_m = re.search(r'(\d+)d', dur_lower)
    hours_m = re.search(r'(\d+)h', dur_lower)
    mins_m = re.search(r'(\d+)m', dur_lower)
    if days_m: total_secs += int(days_m.group(1)) * 86400
    if hours_m: total_secs += int(hours_m.group(1)) * 3600
    if mins_m: total_secs += int(mins_m.group(1)) * 60
    if total_secs == 0 or total_secs > 2419200:
        emb = discord.Embed(
            title="❌ Invalid Duration",
            description="Duration must be between 1m and 28d",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    td = timedelta(seconds=total_secs)
    try:
        await user.timeout(td, reason=reason)
    except discord.Forbidden:
        emb = discord.Embed(
            title="❌ Permission Error",
            description="I don't have permission to mute this user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    except discord.HTTPException as e:
        emb = discord.Embed(
            title="❌ Error",
            description=f"Failed to mute user: {e}",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    sid = interaction.guild.id
    pun_data = {
        "type": "mute",
        "user_id": user.id,
        "user_name": str(user),
        "reason": reason,
        "punished_by": interaction.user.id,
        "punished_by_name": str(interaction.user),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration": duration
    }
    add_punishment(sid, pun_data)
    emb_mod = discord.Embed(
        title="🔇 User Muted",
        description=f"**{user.mention}** has been muted",
        color=Colors.MUTE,
        timestamp=datetime.now(timezone.utc)
    )
    emb_mod.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    emb_mod.add_field(
        name="⏱️ Duration",
        value=format_duration_friendly(duration),
        inline=True
    )
    emb_mod.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    emb_mod.set_footer(
        text="Custos Moderation",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.followup.send(embed=emb_mod, ephemeral=True)
    try:
        emb_dm = discord.Embed(
            title="🔇 You Have Been Muted",
            description=f"You have been muted in **{interaction.guild.name}**",
            color=Colors.MUTE,
            timestamp=datetime.now(timezone.utc)
        )
        emb_dm.add_field(
            name="⏱️ Duration",
            value=format_duration_friendly(duration),
            inline=True
        )
        emb_dm.add_field(
            name="📝 Reason",
            value=f"```{reason}```",
            inline=False
        )
        emb_dm.set_footer(
            text=f"Muted by {interaction.user} | Custos Moderation",
            icon_url="https://i.imgur.com/AfFp7pu.png"
        )
        await user.send(embed=emb_dm)
    except discord.Forbidden:
        pass
    log_emb = discord.Embed(
        title="🔇 User Muted",
        description=f"**{user}** has been muted",
        color=Colors.MUTE,
        timestamp=datetime.now(timezone.utc)
    )
    log_emb.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="👮 Muted By",
        value=f"{interaction.user.mention}\n`ID: {interaction.user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="⏱️ Duration",
        value=format_duration_friendly(duration),
        inline=True
    )
    log_emb.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    log_emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await send_log(interaction.guild, log_emb)
    asyncio.create_task(batch_save_database())

@tree.command(name="unmute", description="Remove timeout from a user")
@app_commands.describe(
    user="User to unmute",
    reason="Reason for unmute"
)
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: Optional[str] = "No reason provided"
):
    await interaction.response.defer(ephemeral=True)
    if not user.is_timed_out():
        emb = discord.Embed(
            title="ℹ️ User Not Muted",
            description=f"{user.mention} is not currently muted.",
            color=Colors.INFO
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    try:
        await user.timeout(None, reason=reason)
    except discord.Forbidden:
        emb = discord.Embed(
            title="❌ Permission Error",
            description="I don't have permission to unmute this user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    emb_mod = discord.Embed(
        title="🔊 User Unmuted",
        description=f"**{user.mention}** has been unmuted",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    emb_mod.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    emb_mod.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    emb_mod.set_footer(
        text="Custos Moderation",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.followup.send(embed=emb_mod, ephemeral=True)
    log_emb = discord.Embed(
        title="🔊 User Unmuted",
        description=f"**{user}** has been unmuted",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    log_emb.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="👮 Unmuted By",
        value=f"{interaction.user.mention}\n`ID: {interaction.user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    log_emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await send_log(interaction.guild, log_emb)

@tree.command(name="ban", description="Ban a user from the server")
@app_commands.describe(
    user="User to ban",
    reason="Reason for ban",
    duration="Duration (e.g., 7d, permanent)"
)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(
    interaction: discord.Interaction,
    user: discord.User,
    reason: str,
    duration: str = "permanent"
):
    await interaction.response.defer(ephemeral=True)
    if user.bot:
        emb = discord.Embed(
            title="❌ Cannot Ban Bot",
            description="You cannot ban a bot user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    if user.id == interaction.user.id:
        emb = discord.Embed(
            title="❌ Cannot Ban Self",
            description="You cannot ban yourself.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    sid = interaction.guild.id
    pun_data = {
        "type": "ban",
        "user_id": user.id,
        "user_name": str(user),
        "reason": reason,
        "punished_by": interaction.user.id,
        "punished_by_name": str(interaction.user),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration": duration
    }
    if duration.lower() != "permanent":
        dur_lower = duration.lower()
        total_secs = 0
        days_m = re.search(r'(\d+)d', dur_lower)
        hours_m = re.search(r'(\d+)h', dur_lower)
        mins_m = re.search(r'(\d+)m', dur_lower)
        if days_m: total_secs += int(days_m.group(1)) * 86400
        if hours_m: total_secs += int(hours_m.group(1)) * 3600
        if mins_m: total_secs += int(mins_m.group(1)) * 60
        if total_secs > 0:
            exp = datetime.now(timezone.utc) + timedelta(seconds=total_secs)
            pun_data["expires_at"] = exp.isoformat()
    add_punishment(sid, pun_data)
    try:
        await interaction.guild.ban(user, reason=reason)
    except discord.Forbidden:
        emb = discord.Embed(
            title="❌ Permission Error",
            description="I don't have permission to ban this user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    except discord.HTTPException as e:
        emb = discord.Embed(
            title="❌ Error",
            description=f"Failed to ban user: {e}",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    emb_mod = discord.Embed(
        title="🔨 User Banned",
        description=f"**{user.mention}** has been banned",
        color=Colors.DANGER,
        timestamp=datetime.now(timezone.utc)
    )
    emb_mod.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    emb_mod.add_field(
        name="⏱️ Duration",
        value=format_duration_friendly(duration),
        inline=True
    )
    emb_mod.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    emb_mod.set_footer(
        text="Custos Moderation",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.followup.send(embed=emb_mod, ephemeral=True)
    log_emb = discord.Embed(
        title="🔨 User Banned",
        description=f"**{user}** has been banned",
        color=Colors.DANGER,
        timestamp=datetime.now(timezone.utc)
    )
    log_emb.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="👮 Banned By",
        value=f"{interaction.user.mention}\n`ID: {interaction.user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="⏱️ Duration",
        value=format_duration_friendly(duration),
        inline=True
    )
    log_emb.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    log_emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await send_log(interaction.guild, log_emb)
    asyncio.create_task(batch_save_database())

@tree.command(name="unban", description="Unban a user from the server")
@app_commands.describe(
    user_id="ID of user to unban",
    reason="Reason for unban"
)
@app_commands.checks.has_permissions(ban_members=True)
async def unban(
    interaction: discord.Interaction,
    user_id: str,
    reason: Optional[str] = "No reason provided"
):
    await interaction.response.defer(ephemeral=True)
    try:
        uid = int(user_id)
    except ValueError:
        emb = discord.Embed(
            title="❌ Invalid User ID",
            description="Please provide a valid user ID.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    try:
        await interaction.guild.unban(discord.Object(id=uid), reason=reason)
    except discord.NotFound:
        emb = discord.Embed(
            title="❌ User Not Banned",
            description="This user is not banned.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    except discord.Forbidden:
        emb = discord.Embed(
            title="❌ Permission Error",
            description="I don't have permission to unban users.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    emb_mod = discord.Embed(
        title="✅ User Unbanned",
        description=f"User with ID `{uid}` has been unbanned",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    emb_mod.add_field(
        name="👤 User ID",
        value=f"`{uid}`",
        inline=True
    )
    emb_mod.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    emb_mod.set_footer(
        text="Custos Moderation",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.followup.send(embed=emb_mod, ephemeral=True)
    log_emb = discord.Embed(
        title="✅ User Unbanned",
        description=f"User `{uid}` has been unbanned",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    log_emb.add_field(
        name="👤 User ID",
        value=f"`{uid}`",
        inline=True
    )
    log_emb.add_field(
        name="👮 Unbanned By",
        value=f"{interaction.user.mention}\n`ID: {interaction.user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    log_emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await send_log(interaction.guild, log_emb)

@tree.command(name="kick", description="Kick a user from the server")
@app_commands.describe(
    user="User to kick",
    reason="Reason for kick"
)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str
):
    await interaction.response.defer(ephemeral=True)
    if user.bot:
        emb = discord.Embed(
            title="❌ Cannot Kick Bot",
            description="You cannot kick a bot user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    if user.id == interaction.user.id:
        emb = discord.Embed(
            title="❌ Cannot Kick Self",
            description="You cannot kick yourself.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    sid = interaction.guild.id
    pun_data = {
        "type": "kick",
        "user_id": user.id,
        "user_name": str(user),
        "reason": reason,
        "punished_by": interaction.user.id,
        "punished_by_name": str(interaction.user),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    add_punishment(sid, pun_data)
    try:
        await user.kick(reason=reason)
    except discord.Forbidden:
        emb = discord.Embed(
            title="❌ Permission Error",
            description="I don't have permission to kick this user.",
            color=Colors.DANGER
        )
        await interaction.followup.send(embed=emb, ephemeral=True)
        return
    emb_mod = discord.Embed(
        title="👢 User Kicked",
        description=f"**{user.mention}** has been kicked",
        color=Colors.WARNING,
        timestamp=datetime.now(timezone.utc)
    )
    emb_mod.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    emb_mod.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    emb_mod.set_footer(
        text="Custos Moderation",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.followup.send(embed=emb_mod, ephemeral=True)
    log_emb = discord.Embed(
        title="👢 User Kicked",
        description=f"**{user}** has been kicked",
        color=Colors.WARNING,
        timestamp=datetime.now(timezone.utc)
    )
    log_emb.add_field(
        name="👤 User",
        value=f"{user.mention}\n`ID: {user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="👮 Kicked By",
        value=f"{interaction.user.mention}\n`ID: {interaction.user.id}`",
        inline=True
    )
    log_emb.add_field(
        name="📝 Reason",
        value=f"```{reason}```",
        inline=False
    )
    log_emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await send_log(interaction.guild, log_emb)
    asyncio.create_task(batch_save_database())

@tree.command(name="history", description="View server moderation history")
async def history(interaction: discord.Interaction):
    sid = interaction.guild.id
    server_puns = punishments.get(sid, [])
    total_warns = sum(
        len(warns) for warns in warnings.get(sid, {}).values()
    )
    emb = discord.Embed(
        title="📊 Server Moderation History",
        description=f"Statistics for **{interaction.guild.name}**",
        color=Colors.INFO,
        timestamp=datetime.now(timezone.utc)
    )
    emb.add_field(
        name="⚠️ Total Warnings",
        value=f"**{total_warns}** warnings issued",
        inline=True
    )
    emb.add_field(
        name="🔨 Total Punishments",
        value=f"**{len(server_puns)}** actions taken",
        inline=True
    )
    type_counts = {}
    for p in server_puns:
        ptype = p.get("type", "unknown")
        type_counts[ptype] = type_counts.get(ptype, 0) + 1
    if type_counts:
        breakdown = "\n".join([
            f"**{ptype.capitalize()}:** {count}"
            for ptype, count in type_counts.items()
        ])
        emb.add_field(
            name="📋 Breakdown",
            value=breakdown,
            inline=False
        )
    if server_puns:
        recent = sorted(
            server_puns,
            key=lambda x: x.get("timestamp", ""),
            reverse=True
        )[:3]
        for p in recent:
            ts = datetime.fromisoformat(p["timestamp"])
            emb.add_field(
                name=f"{p.get('type', 'action').capitalize()} - {p.get('user_name', 'Unknown')}",
                value=f"**By:** {p.get('punished_by_name', 'Unknown')}\n**Date:** <t:{int(ts.timestamp())}:R>",
                inline=False
            )
    emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)

@tree.command(name="setlogchannel", description="Set moderation log channel")
@app_commands.describe(channel="Channel for moderation logs")
@app_commands.checks.has_permissions(manage_guild=True)
async def setlogchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    sid = interaction.guild.id
    if sid not in server_settings:
        server_settings[sid] = {}
    server_settings[sid]["log_channel_id"] = channel.id
    emb = discord.Embed(
        title="✅ Log Channel Set",
        description=f"Moderation logs will be sent to {channel.mention}",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    emb.add_field(
        name="📝 Channel",
        value=f"{channel.mention}\n`ID: {channel.id}`",
        inline=True
    )
    emb.set_footer(
        text="Custos Moderation System",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)
    asyncio.create_task(batch_save_database())

@tree.command(name="help", description="View all available commands")
async def help_cmd(interaction: discord.Interaction):
    emb = discord.Embed(
        title="🛡️ Custos Moderation Commands",
        description="Professional moderation tools for your server",
        color=Colors.PRIMARY
    )
    emb.add_field(
        name="⚠️ Warnings",
        value="```\n/warn - Issue a warning\n/warnings - View user warnings\n/clearwarnings - Clear warnings\n```",
        inline=False
    )
    emb.add_field(
        name="🔨 Moderation",
        value="```\n/mute - Timeout a user\n/unmute - Remove timeout\n/kick - Kick a user\n/ban - Ban a user\n/unban - Unban a user\n```",
        inline=False
    )
    emb.add_field(
        name="⚙️ Settings",
        value="```\n/setlogchannel - Set log channel\n/history - View server stats\n```",
        inline=False
    )
    emb.add_field(
        name="ℹ️ Features",
        value="• Auto-ban at 3 warnings\n• Temporary ban support\n• Detailed logging\n• Server-isolated data",
        inline=False
    )
    emb.set_footer(
        text="Custos Professional Moderation",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)

@tasks.loop(minutes=5)
async def check_temporary_bans():
    now = datetime.now(timezone.utc)
    for guild in bot.guilds:
        sid = guild.id
        if sid not in punishments:
            continue
        for punishment in punishments[sid]:
            if punishment.get("type") != "ban":
                continue
            if "expires_at" not in punishment:
                continue
            if punishment.get("unbanned", False):
                continue
            try:
                exp_at = datetime.fromisoformat(punishment["expires_at"])
                if now >= exp_at:
                    uid = punishment["user_id"]
                    try:
                        await guild.unban(
                            discord.Object(id=uid),
                            reason="Temporary ban expired"
                        )
                        punishment["unbanned"] = True
                        punishment["unbanned_at"] = now.isoformat()
                        log_emb = discord.Embed(
                            title="✅ Temporary Ban Expired",
                            description="**User auto-unbanned**",
                            color=Colors.SUCCESS,
                            timestamp=now
                        )
                        log_emb.add_field(
                            name="👤 User",
                            value=f"<@{uid}>\n`ID: {uid}`",
                            inline=True
                        )
                        log_emb.add_field(
                            name="📝 Original Reason",
                            value=f"```{punishment.get('reason', 'No reason')}```",
                            inline=False
                        )
                        log_emb.add_field(
                            name="👮 Originally Banned By",
                            value=punishment.get("punished_by_name", "Unknown"),
                            inline=True
                        )
                        log_emb.set_footer(
                            text="Automatic System | Custos Moderation",
                            icon_url="https://i.imgur.com/AfFp7pu.png"
                        )
                        await send_log(guild, log_emb)
                        logging.info(
                            f"Unbanned user {uid} in {guild.name}"
                        )
                        asyncio.create_task(batch_save_database())
                    except discord.NotFound:
                        punishment["unbanned"] = True
                        punishment["unbanned_at"] = now.isoformat()
                    except discord.Forbidden:
                        logging.warning(
                            f"No permission to unban {uid} in {guild.name}"
                        )
                    except discord.HTTPException as e:
                        logging.error(f"Error unbanning {uid}: {e}")
            except (ValueError, KeyError) as e:
                logging.warning(f"Invalid punishment data: {e}")
                continue

@bot.event
async def on_ready():
    logging.info(f"🚀 Custos Bot logged in as {bot.user}")
    logging.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    await load_database()
    try:
        synced = await tree.sync()
        logging.info(f"✅ Synced {len(synced)} slash commands")
        cmd_names = [cmd.name for cmd in synced]
        logging.info(f"📝 Commands: {', '.join(cmd_names)}")
    except Exception as e:
        logging.error(f"❌ Failed to sync commands: {e}")
    check_temporary_bans.start()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{len(bot.guilds)} servers | /help"
        )
    )
    logging.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logging.info(f"🎊 Custos Bot is fully operational!")
    logging.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

@bot.event
async def on_guild_join(guild):
    logging.info(f"🆕 Joined new server: {guild.name} ({guild.id})")
    welcome_emb = discord.Embed(
        title="🛡️ Welcome to Custos Professional Moderation",
        description="Thank you for choosing Custos! Ready to help you maintain a safe community.",
        color=Colors.PRIMARY
    )
    welcome_emb.add_field(
        name="🚀 Quick Setup",
        value="```\n1. /setlogchannel #logs\n2. Try /warn, /mute, /ban\n3. Use /history to view stats\n```",
        inline=False
    )
    welcome_emb.add_field(
        name="⚠️ Auto-Moderation",
        value="• Users **auto-banned** at 3 warnings\n• Temporary bans auto-expire\n• All actions logged",
        inline=False
    )
    welcome_emb.add_field(
        name="🔐 Required Permissions",
        value="`Moderate Members` • `Ban Members` • `Administrator`",
        inline=False
    )
    welcome_emb.set_footer(
        text="Custos Professional Moderation • v2.0",
        icon_url="https://i.imgur.com/AfFp7pu.png"
    )
    welcome_emb.set_thumbnail(
        url=bot.user.display_avatar.url if bot.user.display_avatar else None
    )
    target = None
    if guild.system_channel and \
       guild.system_channel.permissions_for(guild.me).send_messages:
        target = guild.system_channel
    else:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                target = ch
                break
    if target:
        try:
            await target.send(embed=welcome_emb)
        except discord.HTTPException:
            pass
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{len(bot.guilds)} servers | /help"
        )
    )

@bot.event
async def on_guild_remove(guild):
    logging.info(f"👋 Left server: {guild.name} ({guild.id})")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{len(bot.guilds)} servers | /help"
        )
    )

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ **Error:** No permission to use this command.",
            delete_after=10
        )
        return
    logging.error(f"Command error in {ctx.guild}: {error}")

@bot.event
async def on_application_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.MissingPermissions):
        if not interaction.response.is_done():
            emb = discord.Embed(
                title="❌ Missing Permissions",
                description="You lack the required permissions.",
                color=Colors.DANGER
            )
            emb.set_footer(text="Custos Moderation System")
            await interaction.response.send_message(
                embed=emb,
                ephemeral=True
            )
        return
    if isinstance(error, app_commands.BotMissingPermissions):
        if not interaction.response.is_done():
            emb = discord.Embed(
                title="❌ Bot Missing Permissions",
                description="I'm missing permissions. Check my role.",
                color=Colors.DANGER
            )
            emb.set_footer(text="Custos Moderation System")
            await interaction.response.send_message(
                embed=emb,
                ephemeral=True
            )
        return
    logging.error(f"Slash command error in {interaction.guild}: {error}")
    if not interaction.response.is_done():
        emb = discord.Embed(
            title="❌ Unexpected Error",
            description="An error occurred. Try again later.",
            color=Colors.DANGER
        )
        emb.set_footer(text="Custos Moderation System")
        await interaction.response.send_message(embed=emb, ephemeral=True)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in the environment.")

keep_alive()
bot.run(DISCORD_TOKEN)

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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Database channel ID
DATABASE_CHANNEL_ID = 1405783830555529389

# Bot branding colors
class Colors:
    PRIMARY = 0x5865F2  # Discord Blurple
    SUCCESS = 0x57F287  # Green
    WARNING = 0xFEE75C  # Yellow
    DANGER = 0xED4245   # Red
    INFO = 0x5865F2     # Blue
    MUTE = 0xEB459E     # Pink

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Global data structures - server-isolated
warnings = {}  # {server_id: {user_id: [warnings]}}
punishments = {}  # {server_id: [punishment_records]}
server_settings = {}  # {server_id: {log_channel_id}}
pending_database_save = False
last_database_save = datetime.now()

def get_user_warnings(server_id: int, user_id: int) -> List[Dict]:
    """Get warnings for a specific user in a server"""
    if server_id not in warnings:
        warnings[server_id] = {}
    if user_id not in warnings[server_id]:
        warnings[server_id][user_id] = []
    return warnings[server_id][user_id]

def add_warning(server_id: int, user_id: int, warning_data: Dict):
    """Add a warning to a user"""
    if server_id not in warnings:
        warnings[server_id] = {}
    if user_id not in warnings[server_id]:
        warnings[server_id][user_id] = []
    warnings[server_id][user_id].append(warning_data)

def add_punishment(server_id: int, punishment_data: Dict):
    """Add a punishment record"""
    if server_id not in punishments:
        punishments[server_id] = []
    punishments[server_id].append(punishment_data)

def get_warning_level_emoji(count: int) -> str:
    """Get emoji based on warning level"""
    if count == 0:
        return "🟢"
    elif count == 1:
        return "🟡"
    elif count == 2:
        return "🟠"
    else:
        return "🔴"

def format_duration_friendly(duration_str: str) -> str:
    """Format duration into friendly text"""
    if duration_str == "permanent":
        return "Permanent"
    
    duration_lower = duration_str.lower()
    result = []
    
    days_match = re.search(r'(\d+)d', duration_lower)
    hours_match = re.search(r'(\d+)h', duration_lower)
    mins_match = re.search(r'(\d+)m', duration_lower)
    
    if days_match:
        days = int(days_match.group(1))
        result.append(f"{days} day{'s' if days > 1 else ''}")
    if hours_match:
        hours = int(hours_match.group(1))
        result.append(f"{hours} hour{'s' if hours > 1 else ''}")
    if mins_match:
        mins = int(mins_match.group(1))
        result.append(f"{mins} minute{'s' if mins > 1 else ''}")
    
    return ", ".join(result) if result else duration_str

async def batch_save_database():
    """Batch database saves to avoid rate limiting"""
    global pending_database_save, last_database_save
    
    if not pending_database_save:
        pending_database_save = True
        await asyncio.sleep(5)
        
        if (datetime.now() - last_database_save).total_seconds() >= 30:
            await save_database()
            last_database_save = datetime.now()
        
        pending_database_save = False

async def load_database():
    """Load data from the database channel with improved error handling"""
    global warnings, punishments, server_settings
    
    try:
        db_channel = bot.get_channel(DATABASE_CHANNEL_ID)
        if not db_channel:
            logging.error(f"Database channel {DATABASE_CHANNEL_ID} not found!")
            warnings = {}
            punishments = {}
            server_settings = {}
            return

        warnings = {}
        punishments = {}
        server_settings = {}
        
        messages_to_check = []
        
        async for message in db_channel.history(limit=50):
            if message.author == bot.user:
                messages_to_check.append(message)
        
        messages_to_check.sort(key=lambda m: m.created_at, reverse=True)
        
        for message in messages_to_check:
            if not message.content:
                continue
                
            if message.content.startswith("```json") and message.content.endswith("```"):
                try:
                    json_content = message.content[7:-3].strip()
                    if not json_content:
                        continue
                        
                    data = json.loads(json_content)
                    
                    if isinstance(data, dict) and "warnings" in data:
                        warnings_data = data.get("warnings", {})
                        if isinstance(warnings_data, dict):
                            for server_id, users in warnings_data.items():
                                try:
                                    server_id_int = int(server_id)
                                    warnings[server_id_int] = {}
                                    
                                    if isinstance(users, dict):
                                        for user_id, user_warnings in users.items():
                                            try:
                                                user_id_int = int(user_id)
                                                if isinstance(user_warnings, list):
                                                    warnings[server_id_int][user_id_int] = user_warnings
                                            except (ValueError, TypeError):
                                                continue
                                except (ValueError, TypeError):
                                    continue
                        
                        punishments_data = data.get("punishments", {})
                        if isinstance(punishments_data, dict):
                            for server_id, punishment_list in punishments_data.items():
                                try:
                                    server_id_int = int(server_id)
                                    if isinstance(punishment_list, list):
                                        punishments[server_id_int] = punishment_list
                                except (ValueError, TypeError):
                                    continue
                        
                        settings_data = data.get("server_settings", {})
                        if isinstance(settings_data, dict):
                            for server_id, settings in settings_data.items():
                                try:
                                    server_id_int = int(server_id)
                                    if isinstance(settings, dict):
                                        server_settings[server_id_int] = settings
                                except (ValueError, TypeError):
                                    continue
                        
                        logging.info(f"✅ Successfully loaded database:")
                        logging.info(f"   📊 {len(warnings)} servers with warnings")
                        logging.info(f"   🔨 {len(punishments)} servers with punishments")
                        logging.info(f"   ⚙️ {len(server_settings)} servers with settings")
                        return
                    
                except json.JSONDecodeError as e:
                    logging.warning(f"JSON decode error in message {message.id}: {e}")
                    continue
                except Exception as e:
                    logging.warning(f"Error processing message {message.id}: {e}")
                    continue
        
        logging.info("🔍 Attempting multi-part database reconstruction...")
        
        part_messages = []
        
        for message in messages_to_check:
            if "Part" in message.content and "```json" in message.content:
                try:
                    part_match = re.search(r'Part (\d+)/(\d+)', message.content)
                    if part_match:
                        part_num = int(part_match.group(1))
                        total_parts = int(part_match.group(2))
                        
                        start = message.content.find("```json") + 7
                        end = message.content.rfind("```")
                        if start > 6 and end > start:
                            json_content = message.content[start:end]
                            part_messages.append((part_num, json_content, message.created_at))
                except Exception as e:
                    logging.warning(f"Error parsing multi-part message {message.id}: {e}")
                    continue
        
        if part_messages:
            part_messages.sort(key=lambda x: x[0])
            combined_json = "".join([content for _, content, _ in part_messages])
            
            try:
                data = json.loads(combined_json)
                
                if isinstance(data, dict) and "warnings" in data:
                    warnings_data = data.get("warnings", {})
                    if isinstance(warnings_data, dict):
                        for server_id, users in warnings_data.items():
                            try:
                                server_id_int = int(server_id)
                                warnings[server_id_int] = {}
                                
                                if isinstance(users, dict):
                                    for user_id, user_warnings in users.items():
                                        try:
                                            user_id_int = int(user_id)
                                            if isinstance(user_warnings, list):
                                                warnings[server_id_int][user_id_int] = user_warnings
                                        except (ValueError, TypeError):
                                            continue
                            except (ValueError, TypeError):
                                continue
                    
                    punishments_data = data.get("punishments", {})
                    if isinstance(punishments_data, dict):
                        for server_id, punishment_list in punishments_data.items():
                            try:
                                server_id_int = int(server_id)
                                if isinstance(punishment_list, list):
                                    punishments[server_id_int] = punishment_list
                            except (ValueError, TypeError):
                                continue
                    
                    settings_data = data.get("server_settings", {})
                    if isinstance(settings_data, dict):
                        for server_id, settings in settings_data.items():
                            try:
                                server_id_int = int(server_id)
                                if isinstance(settings, dict):
                                    server_settings[server_id_int] = settings
                            except (ValueError, TypeError):
                                continue
                    
                    logging.info(f"✅ Reconstructed database from {len(part_messages)} parts:")
                    logging.info(f"   📊 {len(warnings)} servers with warnings")
                    logging.info(f"   🔨 {len(punishments)} servers with punishments")
                    return
                    
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse reconstructed multi-part JSON: {e}")
            except Exception as e:
                logging.error(f"Error processing reconstructed data: {e}")
        
        logging.warning("⚠️ No valid database found, starting with empty database")
        warnings = {}
        punishments = {}
        server_settings = {}
        
    except Exception as e:
        logging.error(f"Critical error loading database: {e}")
        warnings = {}
        punishments = {}
        server_settings = {}

async def save_database():
    """Save all data to the database channel"""
    try:
        db_channel = bot.get_channel(DATABASE_CHANNEL_ID)
        if not db_channel:
            logging.error(f"Database channel {DATABASE_CHANNEL_ID} not found!")
            return

        database_data = {
            "warnings": warnings,
            "punishments": punishments,
            "server_settings": server_settings,
            "metadata": {
                "version": "2.0-custos-professional",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "total_servers": len(warnings),
                "save_timestamp": datetime.now(timezone.utc).timestamp()
            }
        }
        
        json_content = json.dumps(database_data, indent=2, ensure_ascii=False)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        content_hash = hashlib.md5(json_content.encode()).hexdigest()[:8]
        
        if len(json_content) > 1900:
            chunk_size = 1900
            chunks = [json_content[i:i+chunk_size] for i in range(0, len(json_content), chunk_size)]
            
            embed = discord.Embed(
                title="💾 Custos Database Backup",
                description=f"```yaml\nVersion: 2.0-professional\nServers: {database_data['metadata']['total_servers']}\nUpdated: {timestamp}\nParts: {len(chunks)}\nHash: {content_hash}\n```",
                color=Colors.INFO,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
            
            await db_channel.send(embed=embed)
            
            for i, chunk in enumerate(chunks):
                chunk_message = f"**Part {i+1}/{len(chunks)}:**\n```json\n{chunk}\n```"
                await db_channel.send(chunk_message)
                await asyncio.sleep(0.5)
            
            logging.info(f"✅ Database saved in {len(chunks)} parts (hash: {content_hash})")
        else:
            message_content = f"```json\n{json_content}\n```"
            embed = discord.Embed(
                title="💾 Custos Database Backup",
                description=f"```yaml\nVersion: 2.0-professional\nServers: {database_data['metadata']['total_servers']}\nUpdated: {timestamp}\nHash: {content_hash}\n```",
                color=Colors.INFO,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
            
            await db_channel.send(content=message_content, embed=embed)
            logging.info(f"✅ Database saved successfully (hash: {content_hash})")
        
    except discord.HTTPException as e:
        logging.error(f"Discord HTTP error saving database: {e}")
    except Exception as e:
        logging.error(f"Critical error saving database: {e}")

def parse_duration(duration_str: str) -> Optional[timedelta]:
    """Parse duration string like '1d', '2h', '30m' into timedelta"""
    if not duration_str:
        return None
    
    duration_lower = duration_str.lower().strip()
    pattern = r'(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?'
    match = re.fullmatch(pattern, duration_lower)
    
    if match and any(match.groups()):
        days, hours, minutes = match.groups()
        return timedelta(
            days=int(days or 0),
            hours=int(hours or 0),
            minutes=int(minutes or 0)
        )
    
    simple_match = re.fullmatch(r'(\d+)([dhm])', duration_lower)
    if simple_match:
        value = int(simple_match.group(1))
        unit = simple_match.group(2)
        multipliers = {'m': 'minutes', 'h': 'hours', 'd': 'days'}
        return timedelta(**{multipliers[unit]: value})
    
    return None

async def send_log(guild: discord.Guild, embed: discord.Embed):
    """Send a log message to the configured log channel"""
    if guild.id not in server_settings:
        return
    
    log_channel_id = server_settings[guild.id].get("log_channel_id")
    if not log_channel_id:
        return
    
    log_channel = guild.get_channel(log_channel_id)
    if log_channel and isinstance(log_channel, discord.TextChannel):
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

# Moderation Commands

@tree.command(name="warn", description="⚠️ Issue a warning to a user")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(
    user="The user to warn",
    reason="Reason for the warning"
)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    """Warn a user and track warnings"""
    await interaction.response.defer(ephemeral=True)
    
    if user.bot:
        await interaction.followup.send("❌ **Error:** Cannot warn bot accounts.", ephemeral=True)
        return
    
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ **Error:** You cannot warn yourself.", ephemeral=True)
        return
    
    if user.top_role >= interaction.user.top_role:
        await interaction.followup.send("❌ **Error:** Cannot warn users with equal or higher roles.", ephemeral=True)
        return
    
    warning_data = {
        "reason": reason,
        "warned_by": interaction.user.id,
        "warned_by_name": str(interaction.user),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "warning_id": f"{interaction.guild.id}_{user.id}_{int(datetime.now(timezone.utc).timestamp())}"
    }
    
    add_warning(interaction.guild.id, user.id, warning_data)
    user_warnings = get_user_warnings(interaction.guild.id, user.id)
    warning_count = len(user_warnings)
    warning_emoji = get_warning_level_emoji(warning_count)
    
    try:
        action_embed = discord.Embed(color=Colors.WARNING)
        action_embed.add_field(
            name="⚠️ MODERATION ACTION",
            value=f"```yaml\nAction: Warning Issued\nUser: {user.name}\nStaff: {interaction.user.name}\nWarnings: {warning_emoji} {warning_count}/3\nReason: {reason}\n```",
            inline=False
        )
        await interaction.channel.send(embed=action_embed)
    except discord.HTTPException:
        pass
    
    log_embed = discord.Embed(
        title="⚠️ Warning Issued",
        color=Colors.WARNING,
        timestamp=datetime.now(timezone.utc)
    )
    log_embed.set_thumbnail(url=user.display_avatar.url)
    log_embed.add_field(name="👤 User", value=f"{user.mention}\n`{user.name}`\n`ID: {user.id}`", inline=True)
    log_embed.add_field(name="👮 Staff", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
    log_embed.add_field(name="📊 Warning Level", value=f"{warning_emoji} **{warning_count}/3**", inline=True)
    log_embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)
    
    if warning_count >= 2:
        log_embed.add_field(
            name="⚠️ Notice",
            value="**User is approaching automatic ban threshold!**" if warning_count == 2 else "**User has reached maximum warnings!**",
            inline=False
        )
    
    log_embed.set_footer(text=f"Case ID: {warning_data['warning_id']}", icon_url="https://i.imgur.com/AfFp7pu.png")
    
    await send_log(interaction.guild, log_embed)
    
    if warning_count >= 3:
        try:
            await user.ban(reason=f"Automatic ban: 3 warnings reached. Last warning: {reason}")
            
            punishment_data = {
                "type": "ban",
                "user_id": user.id,
                "user_name": str(user),
                "reason": f"Automatic ban: 3 warnings reached",
                "duration": "permanent",
                "punished_by": bot.user.id,
                "punished_by_name": "Custos (Automatic)",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            add_punishment(interaction.guild.id, punishment_data)
            
            try:
                ban_embed = discord.Embed(color=Colors.DANGER)
                ban_embed.add_field(
                    name="🔨 AUTOMATIC BAN EXECUTED",
                    value=f"```yaml\nAction: Permanent Ban\nUser: {user.name}\nReason: 3 Warnings Reached\nSystem: Automatic Enforcement\n```",
                    inline=False
                )
                await interaction.channel.send(embed=ban_embed)
            except discord.HTTPException:
                pass
            
            ban_log_embed = discord.Embed(
                title="🔨 Automatic Ban Executed",
                description="**User has been permanently banned for reaching the warning threshold.**",
                color=Colors.DANGER,
                timestamp=datetime.now(timezone.utc)
            )
            ban_log_embed.set_thumbnail(url=user.display_avatar.url)
            ban_log_embed.add_field(name="👤 User", value=f"{user.mention}\n`{user.name}`\n`ID: {user.id}`", inline=True)
            ban_log_embed.add_field(name="🤖 System", value="Custos Auto-Moderation", inline=True)
            ban_log_embed.add_field(name="⏱️ Duration", value="**Permanent**", inline=True)
            ban_log_embed.add_field(name="📊 Trigger", value="🔴 **3/3 Warnings**", inline=False)
            ban_log_embed.set_footer(text="Automatic Enforcement System", icon_url="https://i.imgur.com/AfFp7pu.png")
            
            await send_log(interaction.guild, ban_log_embed)
            
            await interaction.followup.send(
                f"✅ **Warning issued successfully**\n"
                f"🔨 **User has been automatically banned** for reaching 3/3 warnings!",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.followup.send(
                f"✅ **Warning issued** ({warning_count}/3)\n"
                f"⚠️ **Cannot auto-ban:** Missing permissions!",
                ephemeral=True
            )
    else:
        await interaction.followup.send(
            f"✅ **Warning issued successfully**\n"
            f"📊 User now has {warning_emoji} **{warning_count}/3** warnings",
            ephemeral=True
        )
    
    asyncio.create_task(batch_save_database())
    
    try:
        dm_embed = discord.Embed(
            title="⚠️ Warning Received",
            description=f"You have received a warning in **{interaction.guild.name}**",
            color=Colors.WARNING,
            timestamp=datetime.now(timezone.utc)
        )
        dm_embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)
        dm_embed.add_field(name="📊 Warning Level", value=f"{warning_emoji} **{warning_count}/3**", inline=True)
        dm_embed.add_field(name="👮 Issued By", value=f"`{interaction.user.name}`", inline=True)
        
        if warning_count >= 2:
            dm_embed.add_field(
                name="⚠️ Important Notice",
                value="**You are close to being automatically banned!**\nOne more warning will result in a permanent ban." if warning_count == 2 else "**Maximum warnings reached!**\nYou have been permanently banned.",
                inline=False
            )
        
        dm_embed.set_footer(text=f"Server: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        pass

@tree.command(name="mute", description="🔇 Mute a user for a specified duration")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(
    user="The user to mute",
    duration="Duration (e.g., 1h, 30m, 1d)",
    reason="Reason for the mute"
)
async def mute(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str):
    """Mute a user with timeout"""
    await interaction.response.defer(ephemeral=True)
    
    if user.bot:
        await interaction.followup.send("❌ **Error:** Cannot mute bot accounts.", ephemeral=True)
        return
    
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ **Error:** You cannot mute yourself.", ephemeral=True)
        return
    
    if user.top_role >= interaction.user.top_role:
        await interaction.followup.send("❌ **Error:** Cannot mute users with equal or higher roles.", ephemeral=True)
        return
    
    duration_delta = parse_duration(duration)
    if not duration_delta:
        await interaction.followup.send("❌ **Invalid duration format**\nExamples: `1h`, `30m`, `1d`, `2h30m`", ephemeral=True)
        return
    
    if duration_delta > timedelta(days=28):
        await interaction.followup.send("❌ **Error:** Mute duration cannot exceed 28 days.", ephemeral=True)
        return
    
    try:
        until = datetime.now(timezone.utc) + duration_delta
        await user.timeout(until, reason=reason)
        
        punishment_data = {
            "type": "mute",
            "user_id": user.id,
            "user_name": str(user),
            "reason": reason,
            "duration": duration,
            "punished_by": interaction.user.id,
            "punished_by_name": str(interaction.user),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "expires_at": until.isoformat()
        }
        add_punishment(interaction.guild.id, punishment_data)
        
        try:
            action_embed = discord.Embed(color=Colors.MUTE)
            action_embed.add_field(
                name="🔇 MODERATION ACTION",
                value=f"```yaml\nAction: User Muted\nUser: {user.name}\nStaff: {interaction.user.name}\nDuration: {format_duration_friendly(duration)}\nReason: {reason}\n```",
                inline=False
            )
            await interaction.channel.send(embed=action_embed)
        except discord.HTTPException:
            pass
        
        log_embed = discord.Embed(
            title="🔇 User Muted",
            color=Colors.MUTE,
            timestamp=datetime.now(timezone.utc)
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        log_embed.add_field(name="👤 User", value=f"{user.mention}\n`{user.name}`\n`ID: {user.id}`", inline=True)
        log_embed.add_field(name="👮 Moderator", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
        log_embed.add_field(name="⏱️ Duration", value=f"**{format_duration_friendly(duration)}**", inline=True)
        log_embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)
        log_embed.add_field(name="⏰ Expires", value=f"<t:{int(until.timestamp())}:F>\n<t:{int(until.timestamp())}:R>", inline=False)
        log_embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
        
        await send_log(interaction.guild, log_embed)
        await interaction.followup.send(
            f"✅ **User muted successfully**\n"
            f"⏱️ Duration: **{format_duration_friendly(duration)}**\n"
            f"⏰ Expires: <t:{int(until.timestamp())}:R>",
            ephemeral=True
        )
        
        asyncio.create_task(batch_save_database())
        
        try:
            dm_embed = discord.Embed(
                title="🔇 You Have Been Muted",
                description=f"You have been muted in **{interaction.guild.name}**",
                color=Colors.MUTE,
                timestamp=datetime.now(timezone.utc)
            )
            dm_embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)
            dm_embed.add_field(name="⏱️ Duration", value=f"**{format_duration_friendly(duration)}**", inline=True)
            dm_embed.add_field(name="⏰ Expires", value=f"<t:{int(until.timestamp())}:R>", inline=True)
            dm_embed.add_field(name="👮 Issued By", value=f"`{interaction.user.name}`", inline=False)
            dm_embed.set_footer(text=f"Server: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass
        
    except discord.Forbidden:
        await interaction.followup.send("❌ **Error:** Missing permissions to mute this user.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ **Error:** {e}", ephemeral=True)

@tree.command(name="unmute", description="🔊 Remove mute from a user")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(user="The user to unmute")
async def unmute(interaction: discord.Interaction, user: discord.Member):
    """Remove timeout from a user"""
    await interaction.response.defer(ephemeral=True)
    
    if not user.is_timed_out():
        await interaction.followup.send(f"❌ **Error:** {user.mention} is not currently muted.", ephemeral=True)
        return
    
    try:
        await user.timeout(None, reason=f"Unmuted by {interaction.user}")
        
        try:
            action_embed = discord.Embed(color=Colors.SUCCESS)
            action_embed.add_field(
                name="🔊 MODERATION ACTION",
                value=f"```yaml\nAction: User Unmuted\nUser: {user.name}\nStaff: {interaction.user.name}\n```",
                inline=False
            )
            await interaction.channel.send(embed=action_embed)
        except discord.HTTPException:
            pass
        
        log_embed = discord.Embed(
            title="🔊 User Unmuted",
            description="**Mute has been removed**",
            color=Colors.SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        log_embed.add_field(name="👤 User", value=f"{user.mention}\n`{user.name}`\n`ID: {user.id}`", inline=True)
        log_embed.add_field(name="👮 Moderator", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
        log_embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
        
        await send_log(interaction.guild, log_embed)
        await interaction.followup.send(f"✅ **{user.mention} has been unmuted**", ephemeral=True)
        
        try:
            dm_embed = discord.Embed(
                title="🔊 You Have Been Unmuted",
                description=f"Your mute has been removed in **{interaction.guild.name}**",
                color=Colors.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            dm_embed.add_field(name="👮 Unmuted By", value=f"`{interaction.user.name}`", inline=False)
            dm_embed.set_footer(text=f"Server: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass
        
    except discord.Forbidden:
        await interaction.followup.send("❌ **Error:** Missing permissions to unmute this user.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ **Error:** {e}", ephemeral=True)

@tree.command(name="ban", description="🔨 Ban a user from the server")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(
    user="The user to ban",
    duration="Duration (e.g., 1d, 7d) - leave empty for permanent",
    reason="Reason for the ban"
)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str, duration: Optional[str] = None):
    """Ban a user temporarily or permanently"""
    await interaction.response.defer(ephemeral=True)
    
    if user.bot:
        await interaction.followup.send("❌ **Error:** Cannot ban bot accounts.", ephemeral=True)
        return
    
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ **Error:** You cannot ban yourself.", ephemeral=True)
        return
    
    if user.top_role >= interaction.user.top_role:
        await interaction.followup.send("❌ **Error:** Cannot ban users with equal or higher roles.", ephemeral=True)
        return
    
    duration_delta = None
    if duration:
        duration_delta = parse_duration(duration)
        if not duration_delta:
            await interaction.followup.send("❌ **Invalid duration format**\nExamples: `1d`, `7d`, `30d`", ephemeral=True)
            return
    
    try:
        await user.ban(reason=reason, delete_message_days=1)
        
        punishment_data = {
            "type": "ban",
            "user_id": user.id,
            "user_name": str(user),
            "reason": reason,
            "duration": duration if duration else "permanent",
            "punished_by": interaction.user.id,
            "punished_by_name": str(interaction.user),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        if duration_delta:
            expires_at = datetime.now(timezone.utc) + duration_delta
            punishment_data["expires_at"] = expires_at.isoformat()
        
        add_punishment(interaction.guild.id, punishment_data)
        
        try:
            action_embed = discord.Embed(color=Colors.DANGER)
            duration_text = format_duration_friendly(duration) if duration else "Permanent"
            action_embed.add_field(
                name="🔨 MODERATION ACTION",
                value=f"```yaml\nAction: User Banned\nUser: {user.name}\nStaff: {interaction.user.name}\nDuration: {duration_text}\nReason: {reason}\n```",
                inline=False
            )
            await interaction.channel.send(embed=action_embed)
        except discord.HTTPException:
            pass
        
        log_embed = discord.Embed(
            title=f"🔨 User Banned {'(Temporary)' if duration else '(Permanent)'}",
            color=Colors.DANGER,
            timestamp=datetime.now(timezone.utc)
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        log_embed.add_field(name="👤 User", value=f"{user.mention}\n`{user.name}`\n`ID: {user.id}`", inline=True)
        log_embed.add_field(name="👮 Staff", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
        log_embed.add_field(name="⏱️ Duration", value=f"**{format_duration_friendly(duration) if duration else 'Permanent'}**", inline=True)
        log_embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)
        
        if duration_delta:
            expires_at = datetime.now(timezone.utc) + duration_delta
            log_embed.add_field(name="⏰ Expires", value=f"<t:{int(expires_at.timestamp())}:F>\n<t:{int(expires_at.timestamp())}:R>", inline=False)
        
        log_embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
        
        await send_log(interaction.guild, log_embed)
        
        duration_text = f"**{format_duration_friendly(duration)}**" if duration else "**permanently**"
        await interaction.followup.send(
            f"✅ **User banned successfully**\n"
            f"⏱️ Duration: {duration_text}",
            ephemeral=True
        )
        
        asyncio.create_task(batch_save_database())
        
        try:
            dm_embed = discord.Embed(
                title="🔨 You Have Been Banned",
                description=f"You have been banned from **{interaction.guild.name}**",
                color=Colors.DANGER,
                timestamp=datetime.now(timezone.utc)
            )
            dm_embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)
            dm_embed.add_field(name="⏱️ Duration", value=f"**{format_duration_friendly(duration) if duration else 'Permanent'}**", inline=True)
            dm_embed.add_field(name="👮 Issued By", value=f"`{interaction.user.name}`", inline=True)
            dm_embed.set_footer(text=f"Server: {interaction.guild.name}", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass
        
    except discord.Forbidden:
        await interaction.followup.send("❌ **Error:** Missing permissions to ban this user.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ **Error:** {e}", ephemeral=True)

@tree.command(name="unban", description="✅ Remove a ban from a user")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user_id="The ID of the user to unban")
async def unban(interaction: discord.Interaction, user_id: str):
    """Unban a user by their ID"""
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id_int = int(user_id)
    except ValueError:
        await interaction.followup.send("❌ **Invalid user ID**\nPlease provide a valid numeric user ID.", ephemeral=True)
        return
    
    try:
        user = await bot.fetch_user(user_id_int)
        await interaction.guild.unban(user, reason=f"Unbanned by {interaction.user}")
        
        try:
            action_embed = discord.Embed(color=Colors.SUCCESS)
            action_embed.add_field(
                name="✅ MODERATION ACTION",
                value=f"```yaml\nAction: User Unbanned\nUser: {user.name}\nStaff: {interaction.user.name}\n```",
                inline=False
            )
            await interaction.channel.send(embed=action_embed)
        except discord.HTTPException:
            pass
        
        log_embed = discord.Embed(
            title="✅ User Unbanned",
            description="**Ban has been removed**",
            color=Colors.SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        log_embed.set_thumbnail(url=user.display_avatar.url)
        log_embed.add_field(name="👤 User", value=f"{user.mention}\n`{user.name}`\n`ID: {user.id}`", inline=True)
        log_embed.add_field(name="👮 Moderator", value=f"{interaction.user.mention}\n`{interaction.user.name}`", inline=True)
        log_embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
        
        await send_log(interaction.guild, log_embed)
        await interaction.followup.send(f"✅ **{user.mention} has been unbanned**", ephemeral=True)
        
    except discord.NotFound:
        await interaction.followup.send("❌ **Error:** This user is not banned or doesn't exist.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ **Error:** Missing permissions to unban users.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ **Error:** {e}", ephemeral=True)

@tree.command(name="warnings", description="📊 Check warnings for a user")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(user="The user to check warnings for")
async def check_warnings(interaction: discord.Interaction, user: discord.Member):
    """Check how many warnings a user has"""
    await interaction.response.defer(ephemeral=True)
    
    user_warnings = get_user_warnings(interaction.guild.id, user.id)
    warning_count = len(user_warnings)
    warning_emoji = get_warning_level_emoji(warning_count)
    
    if not user_warnings:
        embed = discord.Embed(
            title="✅ Clean Record",
            description=f"{user.mention} has no warnings on record.",
            color=Colors.SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"⚠️ Warning History: {user.name}",
        description=f"**Status:** {warning_emoji} **{warning_count}/3 Warnings**",
        color=Colors.WARNING if warning_count < 3 else Colors.DANGER,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    
    for i, warning in enumerate(user_warnings[-5:], 1):
        timestamp = datetime.fromisoformat(warning['timestamp'])
        embed.add_field(
            name=f"━━━━━━━━ Warning #{len(user_warnings) - 5 + i if len(user_warnings) > 5 else i} ━━━━━━━━",
            value=f"**📝 Reason:** {warning['reason']}\n"
                  f"**⏰ Date:** <t:{int(timestamp.timestamp())}:F> (<t:{int(timestamp.timestamp())}:R>)\n"
                  f"**👮 Issued By:** <@{warning['warned_by']}>",
            inline=False
        )
    
    if len(user_warnings) > 5:
        embed.add_field(
            name="ℹ️ Note",
            value=f"Showing latest 5 of {len(user_warnings)} total warnings.",
            inline=False
        )
    
    embed.set_footer(text=f"User ID: {user.id} | Custos Moderation", icon_url="https://i.imgur.com/AfFp7pu.png")
    await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="history", description="📋 View moderation history")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(user="Optional: specific user to view history for")
async def history(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    """View moderation history"""
    await interaction.response.defer(ephemeral=True)
    
    if user:
        user_warnings = get_user_warnings(interaction.guild.id, user.id)
        user_punishments = []
        
        if interaction.guild.id in punishments:
            user_punishments = [p for p in punishments[interaction.guild.id] if p.get("user_id") == user.id]
        
        if not user_warnings and not user_punishments:
            embed = discord.Embed(
                title="✅ Clean Record",
                description=f"{user.mention} has no moderation history.",
                color=Colors.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        warning_emoji = get_warning_level_emoji(len(user_warnings))
        
        embed = discord.Embed(
            title=f"📋 Complete Moderation History",
            description=f"**User:** {user.mention}\n**Warnings:** {warning_emoji} {len(user_warnings)}/3\n**Total Punishments:** {len(user_punishments)}",
            color=Colors.INFO,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        if user_warnings:
            warning_text = ""
            for i, warning in enumerate(user_warnings[-5:], 1):
                timestamp = datetime.fromisoformat(warning['timestamp'])
                warning_text += f"**{i}.** {warning['reason'][:50]}{'...' if len(warning['reason']) > 50 else ''}\n"
                warning_text += f"└ <t:{int(timestamp.timestamp())}:R> by <@{warning['warned_by']}>\n"
            embed.add_field(name="⚠️ Recent Warnings", value=warning_text, inline=False)
        
        if user_punishments:
            punishment_text = ""
            for i, punishment in enumerate(user_punishments[-5:], 1):
                timestamp = datetime.fromisoformat(punishment['timestamp'])
                p_type = punishment['type'].upper()
                duration = format_duration_friendly(punishment.get('duration', 'N/A'))
                punishment_text += f"**{i}.** `{p_type}` ({duration})\n"
                punishment_text += f"└ {punishment['reason'][:40]}{'...' if len(punishment['reason']) > 40 else ''} - <t:{int(timestamp.timestamp())}:R>\n"
            embed.add_field(name="🔨 Recent Punishments", value=punishment_text, inline=False)
        
        embed.set_footer(text=f"User ID: {user.id} | Custos Moderation", icon_url="https://i.imgur.com/AfFp7pu.png")
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    else:
        if interaction.guild.id not in warnings or not warnings[interaction.guild.id]:
            embed = discord.Embed(
                title="✅ Clean Server",
                description="No users have been warned in this server.",
                color=Colors.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        total_users = len(warnings[interaction.guild.id])
        total_punishments = len(punishments.get(interaction.guild.id, []))
        
        embed = discord.Embed(
            title="📊 Server Moderation Statistics",
            description=f"**Total Users Warned:** {total_users}\n**Total Punishments:** {total_punishments}",
            color=Colors.INFO,
            timestamp=datetime.now(timezone.utc)
        )
        
        sorted_users = sorted(
            warnings[interaction.guild.id].items(),
            key=lambda x: len(x[1]),
            reverse=True
        )
        
        user_list = ""
        for i, (user_id, user_warns) in enumerate(sorted_users[:10], 1):
            warning_emoji = get_warning_level_emoji(len(user_warns))
            user_list += f"**{i}.** <@{user_id}> {warning_emoji} **{len(user_warns)}** warning(s)\n"
        
        embed.add_field(name="⚠️ Top Users by Warnings", value=user_list or "None", inline=False)
        
        if total_punishments > 0:
            mutes = sum(1 for p in punishments.get(interaction.guild.id, []) if p.get('type') == 'mute')
            bans = sum(1 for p in punishments.get(interaction.guild.id, []) if p.get('type') == 'ban')
            
            embed.add_field(name="🔨 Punishment Breakdown", value=f"🔇 Mutes: **{mutes}**\n🔨 Bans: **{bans}**", inline=True)
        
        embed.set_footer(text=f"Server ID: {interaction.guild.id} | Custos Moderation", icon_url="https://i.imgur.com/AfFp7pu.png")
        await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="clearwarnings", description="🗑️ Clear all warnings for a user")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(user="The user to clear warnings for")
async def clear_warnings(interaction: discord.Interaction, user: discord.Member):
    """Clear all warnings for a user"""
    await interaction.response.defer(ephemeral=True)
    
    if interaction.guild.id in warnings and user.id in warnings[interaction.guild.id]:
        warning_count = len(warnings[interaction.guild.id][user.id])
        del warnings[interaction.guild.id][user.id]
        asyncio.create_task(batch_save_database())
        
        embed = discord.Embed(
            title="✅ Warnings Cleared",
            description=f"Successfully cleared **{warning_count}** warning(s) for {user.mention}",
            color=Colors.SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="👮 Cleared By", value=interaction.user.mention, inline=True)
        embed.add_field(name="👤 User", value=user.mention, inline=True)
        embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(f"ℹ️ {user.mention} has no warnings to clear.", ephemeral=True)

@tree.command(name="setlogchannel", description="⚙️ Configure moderation log channel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel where detailed moderation logs will be sent")
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the log channel for detailed moderation embeds"""
    await interaction.response.defer(ephemeral=True)
    
    if interaction.guild.id not in server_settings:
        server_settings[interaction.guild.id] = {}
    
    server_settings[interaction.guild.id]["log_channel_id"] = channel.id
    asyncio.create_task(batch_save_database())
    
    embed = discord.Embed(
        title="✅ Log Channel Configured",
        description=f"Moderation logs will now be sent to {channel.mention}",
        color=Colors.SUCCESS,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="📍 Channel", value=channel.mention, inline=True)
    embed.add_field(name="👮 Configured By", value=interaction.user.mention, inline=True)
    embed.set_footer(text="Custos Moderation System", icon_url="https://i.imgur.com/AfFp7pu.png")
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@tasks.loop(minutes=5)
async def check_temporary_bans():
    """Check and unban users with expired temporary bans"""
    now = datetime.now(timezone.utc)
    
    for server_id, punishment_list in list(punishments.items()):
        guild = bot.get_guild(server_id)
        if not guild:
            continue
        
        for punishment in punishment_list:
            if punishment.get("type") != "ban":
                continue
            
            if "expires_at" not in punishment:
                continue
            
            if punishment.get("unbanned", False):
                continue
            
            try:
                expires_at = datetime.fromisoformat(punishment["expires_at"])
                
                if now >= expires_at:
                    user_id = punishment["user_id"]
                    
                    try:
                        await guild.unban(discord.Object(id=user_id), reason="Temporary ban expired")
                        
                        punishment["unbanned"] = True
                        punishment["unbanned_at"] = now.isoformat()
                        
                        log_embed = discord.Embed(
                            title="✅ Temporary Ban Expired",
                            description="**User has been automatically unbanned**",
                            color=Colors.SUCCESS,
                            timestamp=now
                        )
                        log_embed.add_field(name="👤 User", value=f"<@{user_id}>\n`ID: {user_id}`", inline=True)
                        log_embed.add_field(name="📝 Original Reason", value=f"```{punishment.get('reason', 'No reason')}```", inline=False)
                        log_embed.add_field(name="👮 Originally Banned By", value=punishment.get("punished_by_name", "Unknown"), inline=True)
                        log_embed.set_footer(text="Automatic System | Custos Moderation", icon_url="https://i.imgur.com/AfFp7pu.png")
                        
                        await send_log(guild, log_embed)
                        logging.info(f"Unbanned user {user_id} in guild {guild.name} - temporary ban expired")
                        
                        asyncio.create_task(batch_save_database())
                        
                    except discord.NotFound:
                        punishment["unbanned"] = True
                        punishment["unbanned_at"] = now.isoformat()
                    except discord.Forbidden:
                        logging.warning(f"No permission to unban user {user_id} in guild {guild.name}")
                    except discord.HTTPException as e:
                        logging.error(f"Error unbanning user {user_id}: {e}")
                        
            except (ValueError, KeyError) as e:
                logging.warning(f"Invalid punishment data: {e}")
                continue

@bot.event
async def on_ready():
    """Bot startup"""
    logging.info(f"🚀 Custos Bot logged in as {bot.user}")
    logging.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    await load_database()
    
    try:
        synced = await tree.sync()
        logging.info(f"✅ Synced {len(synced)} slash commands")
        command_names = [cmd.name for cmd in synced]
        logging.info(f"📝 Commands: {', '.join(command_names)}")
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
    """Handle bot joining a guild"""
    logging.info(f"🆕 Joined new server: {guild.name} ({guild.id})")
    
    welcome_embed = discord.Embed(
        title="🛡️ Welcome to Custos Professional Moderation",
        description="Thank you for choosing Custos! I'm ready to help you maintain a safe and organized community.",
        color=Colors.PRIMARY
    )
    
    welcome_embed.add_field(
        name="🚀 Quick Setup",
        value="```\n1. /setlogchannel #logs\n   Set where detailed logs are sent\n\n2. Try /warn, /mute, /ban\n   Modern moderation tools\n\n3. Use /history to view stats\n   Track server moderation\n```",
        inline=False
    )
    
    welcome_embed.add_field(
        name="⚠️ Auto-Moderation",
        value="• Users are **automatically banned** at 3 warnings\n• Temporary bans auto-expire\n• All actions are logged and tracked",
        inline=False
    )
    
    welcome_embed.add_field(
        name="🔐 Required Permissions",
        value="`Moderate Members` • `Ban Members` • `Administrator`",
        inline=False
    )
    
    welcome_embed.set_footer(text="Custos Professional Moderation • Version 2.0", icon_url="https://i.imgur.com/AfFp7pu.png")
    welcome_embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user.display_avatar else None)
    
    target_channel = None
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        target_channel = guild.system_channel
    else:
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                target_channel = channel
                break
    
    if target_channel:
        try:
            await target_channel.send(embed=welcome_embed)
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
    """Handle bot leaving a guild"""
    logging.info(f"👋 Left server: {guild.name} ({guild.id})")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{len(bot.guilds)} servers | /help"
        )
    )

@bot.event
async def on_command_error(ctx, error):
    """Error handling for commands"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ **Error:** You don't have permission to use this command.", delete_after=10)
        return
    
    logging.error(f"Command error in {ctx.guild}: {error}")

@bot.event
async def on_application_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Error handling for slash commands"""
    if isinstance(error, app_commands.MissingPermissions):
        if not interaction.response.is_done():
            embed = discord.Embed(
                title="❌ Missing Permissions",
                description="You don't have the required permissions to use this command.",
                color=Colors.DANGER
            )
            embed.set_footer(text="Custos Moderation System")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if isinstance(error, app_commands.BotMissingPermissions):
        if not interaction.response.is_done():
            embed = discord.Embed(
                title="❌ Bot Missing Permissions",
                description="I'm missing permissions to perform this action. Please check my role permissions.",
                color=Colors.DANGER
            )
            embed.set_footer(text="Custos Moderation System")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    logging.error(f"Slash command error in {interaction.guild}: {error}")
    
    if not interaction.response.is_done():
        embed = discord.Embed(
            title="❌ Unexpected Error",
            description="An unexpected error occurred. Please try again later.",
            color=Colors.DANGER
        )
        embed.set_footer(text="Custos Moderation System")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Get Discord token and start the bot
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in the environment.")

# Start keep alive system and run the bot
keep_alive()
bot.run(DISCORD_TOKEN)

import discord
from discord.ext import commands
from google import genai
from google.genai import types
import os
import json
import logging
import asyncio
import re
import time
from typing import Optional, Dict, List

class AIModeration:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        
        if not self.gemini_api_key:
            logging.warning("⚠️ GEMINI_API_KEY not found! AI moderation will be disabled.")
            self.enabled = False
            return
        
        # Configure Gemini with new API
        self.client = genai.Client(api_key=self.gemini_api_key)
        self.model_name = 'gemini-1.5-flash'  # Stable model that works with new API
        self.enabled = True
        
        # Server-specific AI settings
        # Format: {guild_id: {"enabled": bool, "rules": str, "analyzed": bool}}
        self.server_ai_config = {}
        
        # Rate limiting: track last message check per user
        self.user_check_cooldown = {}  # {guild_id: {user_id: timestamp}}
        self.cooldown_seconds = 2  # Don't check same user more than once per 2 seconds
        
        logging.info("✅ AI Moderation module initialized with Gemini 1.5 Flash")
    
    def is_ai_enabled(self, guild_id: int) -> bool:
        """Check if AI moderation is enabled for a server"""
        if not self.enabled:
            return False
        return self.server_ai_config.get(guild_id, {}).get("enabled", False)
    
    def set_ai_enabled(self, guild_id: int, enabled: bool):
        """Enable or disable AI moderation for a server"""
        if guild_id not in self.server_ai_config:
            self.server_ai_config[guild_id] = {"enabled": False, "rules": "", "analyzed": False}
        self.server_ai_config[guild_id]["enabled"] = enabled
        logging.info(f"AI moderation {'enabled' if enabled else 'disabled'} for guild {guild_id}")
    
    async def analyze_server_rules(self, guild: discord.Guild) -> Optional[str]:
        """
        Automatically find and analyze server rules when bot joins
        Searches for rules in:
        1. Channel named 'rules' or 'server-rules'
        2. Channel with 'rule' in name
        3. Server description
        """
        rules_text = ""
        
        # Try to find rules channel
        rules_channels = [
            ch for ch in guild.text_channels 
            if 'rule' in ch.name.lower() and ch.permissions_for(guild.me).read_messages
        ]
        
        if rules_channels:
            # Prefer exact matches first
            exact_match = next((ch for ch in rules_channels if ch.name.lower() in ['rules', 'server-rules']), None)
            rules_channel = exact_match or rules_channels[0]
            
            try:
                # Get last 5 messages from rules channel
                messages = []
                async for msg in rules_channel.history(limit=5):
                    if msg.content:
                        messages.append(msg.content)
                
                if messages:
                    rules_text = "\n\n".join(reversed(messages))
                    logging.info(f"📜 Found rules in #{rules_channel.name} for {guild.name}")
            except discord.Forbidden:
                logging.warning(f"⚠️ No permission to read #{rules_channel.name}")
        
        # Fallback: check server description
        if not rules_text and guild.description:
            rules_text = guild.description
            logging.info(f"📜 Using server description as rules for {guild.name}")
        
        # If still no rules found
        if not rules_text:
            logging.info(f"⚠️ No rules found for {guild.name}, using default rules")
            rules_text = """
            Default Server Rules:
            1. Be respectful to all members
            2. No spam or excessive self-promotion
            3. No hate speech, discrimination, or harassment
            4. Keep content appropriate for all ages
            5. Follow Discord's Terms of Service and Community Guidelines
            """
        
        # Store rules in config
        if guild.id not in self.server_ai_config:
            self.server_ai_config[guild.id] = {"enabled": False, "rules": "", "analyzed": False}
        
        self.server_ai_config[guild.id]["rules"] = rules_text
        self.server_ai_config[guild.id]["analyzed"] = True
        
        return rules_text
    
    async def check_message_violations(
        self, 
        message: discord.Message,
        rules: str
    ) -> Optional[Dict]:
        """
        Use Gemini AI to check if a message violates server rules
        
        Returns:
            Dict with violation info if found, None otherwise
            {
                "violates": bool,
                "rule_broken": str,
                "severity": str,  # "low", "medium", "high"
                "reason": str,
                "suggested_action": str  # "warn", "timeout", "kick", "ban"
            }
        """
        if not self.enabled:
            return None
        
        prompt = f"""You are a Discord server moderation AI assistant. Analyze the following message against the server rules.

**Server Rules:**
{rules}

**Message to analyze:**
"{message.content}"

**Instructions:**
- Determine if the message violates any server rules
- Be reasonable - don't flag minor issues or obvious jokes between friends
- Consider context: casual conversation should be allowed
- Only flag clear violations

Respond ONLY with valid JSON in this exact format (no markdown, no extra text):
{{
  "violates": true or false,
  "rule_broken": "brief description of which rule (or null if no violation)",
  "severity": "low/medium/high (or null if no violation)",
  "reason": "brief 1-2 sentence explanation (or null if no violation)",
  "suggested_action": "warn/timeout/kick/ban (or null if no violation)"
}}"""

        try:
            # FIX #2: Initialize response_text to avoid undefined error
            response_text = ""
            
            # Call Gemini API with new client
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_name,
                contents=prompt
            )
            
            # Parse response
            response_text = response.text.strip()
            
            # Remove markdown code blocks if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            response_text = response_text.strip()
            
            # Parse JSON
            analysis = json.loads(response_text)
            
            # Validate response
            if not isinstance(analysis, dict):
                logging.error(f"Invalid AI response format: {response_text}")
                return None
            
            return analysis
            
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse AI response as JSON: {e}")
            logging.error(f"Response was: {response_text}")
            return None
        # FIX #5: Handle Gemini API rate limits and quota errors
        except Exception as e:
            error_msg = str(e).lower()
            if 'rate limit' in error_msg or '429' in error_msg:
                logging.warning(f"⚠️ Gemini API rate limit hit: {e}")
            elif 'quota' in error_msg or 'exceeded' in error_msg:
                logging.error(f"❌ Gemini API quota exceeded: {e}")
            else:
                logging.error(f"Error checking message with AI: {e}")
            return None
    
    def should_check_message(self, guild_id: int, user_id: int) -> bool:
        """Rate limiting: check if we should analyze this user's message"""
        if guild_id not in self.user_check_cooldown:
            self.user_check_cooldown[guild_id] = {}
        
        now = time.time()
        last_check = self.user_check_cooldown[guild_id].get(user_id, 0)
        
        if now - last_check < self.cooldown_seconds:
            return False
        
        self.user_check_cooldown[guild_id][user_id] = now
        return True
    
    async def handle_violation(
        self,
        message: discord.Message,
        analysis: Dict,
        warn_callback,  # Function to call existing /warn command
        add_warning_callback  # Function to add warning to database
    ):
        """
        Handle a detected rule violation
        Uses the existing warning system from main.py
        """
        try:
            severity = analysis.get("severity", "medium")
            rule_broken = analysis.get("rule_broken", "Unknown rule")
            reason = analysis.get("reason", "AI detected rule violation")
            
            # Create AI-generated warning reason
            ai_reason = f"🤖 AI Auto-Mod: {rule_broken}\n{reason}"
            
            # FIX #6: Safe handling of bot.user (could be None during init)
            bot_id = self.bot.user.id if self.bot.user else 0
            bot_name = self.bot.user.name if self.bot.user else "Custos AI"
            
            # Add warning using existing system
            warning_data = {
                "reason": ai_reason,
                "warned_by": bot_id,
                "warned_by_name": f"{bot_name} (AI)",
                "timestamp": discord.utils.utcnow().isoformat(),
                "severity": severity
            }
            
            # Call the callback to add warning
            add_warning_callback(message.guild.id, message.author.id, warning_data)
            
            # Send warning message to user
            warning_embed = discord.Embed(
                title="⚠️ Automated Warning",
                description=f"{message.author.mention}, your message has been flagged by our AI moderation system.",
                color=0xFEE75C
            )
            warning_embed.add_field(
                name="🚫 Rule Violated",
                value=f"```{rule_broken}```",
                inline=False
            )
            warning_embed.add_field(
                name="📝 Reason",
                value=f"```{reason}```",
                inline=False
            )
            warning_embed.add_field(
                name="⚡ Severity",
                value=f"`{severity.upper()}`",
                inline=True
            )
            warning_embed.set_footer(
                text="🤖 AI-Powered Moderation | If this is a mistake, contact a moderator"
            )
            
            # Try to delete the offending message
            try:
                await message.delete()
                warning_embed.add_field(
                    name="🗑️ Action Taken",
                    value="Message deleted",
                    inline=True
                )
            except discord.Forbidden:
                warning_embed.add_field(
                    name="⚠️ Note",
                    value="Could not delete message (missing permissions)",
                    inline=True
                )
            except discord.NotFound:
                pass
            
            # Send warning in channel
            await message.channel.send(embed=warning_embed, delete_after=15)
            
            logging.info(
                f"🤖 AI Auto-warned {message.author} in {message.guild.name} "
                f"for: {rule_broken}"
            )
            
        except Exception as e:
            logging.error(f"Error handling AI violation: {e}")

def setup(bot: commands.Bot):
    """Setup function to initialize AI moderation"""
    return AIModeration(bot)

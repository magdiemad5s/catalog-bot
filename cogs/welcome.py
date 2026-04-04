"""
cogs/welcome.py — Discord Bot Welcome System
Handles onboarding across 3 stages, rate-limiting, and AI DM interviews.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from google import genai
from google.genai import types

from db.client import get_db

log = logging.getLogger(__name__)

class Welcome(commands.Cog, name="Welcome"):
    """🎉 Handles new member onboarding smoothly and safely."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.join_queue = asyncio.Queue()
        self.active_onboarding = {} # user_id -> list of types.Content (conversation history)
        
        # Controlled by Web Panel
        self.welcoming_enabled = True
        
        self.primary_clients = []
        self.fallback_client = None
        self.current_client_idx = 0
        
        # Setup API Keys for Gemini
        key1 = getattr(self.bot.config, "gemini_api_key", None)
        key2 = getattr(self.bot.config, "gemini_api_key_2", None)
        key3 = getattr(self.bot.config, "gemini_api_key_3", None)
        
        try:
            if key1: self.primary_clients.append(genai.Client(api_key=key1))
            if key2: self.primary_clients.append(genai.Client(api_key=key2))
            if key3: self.fallback_client = genai.Client(api_key=key3)
        except Exception as e:
            log.error(f"Failed to load keys in Welcome Cog: {e}")

        # Start the queue worker
        self.worker_task = self.bot.loop.create_task(self.join_worker())

    def get_client(self):
        """Round-robins the primary keys, or yields the fallback."""
        if not self.primary_clients:
            return self.fallback_client
        client = self.primary_clients[self.current_client_idx]
        self.current_client_idx = (self.current_client_idx + 1) % len(self.primary_clients)
        return client

    async def cog_unload(self):
        """Cleanup worker on unload."""
        self.worker_task.cancel()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Listener: immediately queue newly joined members."""
        if not self.welcoming_enabled:
            return
            
        if member.bot:
            return

        await self.join_queue.put(member)
        log.info(f"Queued {member.name} ({member.id}) for onboarding.")

    async def join_worker(self):
        """Background process that handles the first touches to respect rate limits."""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                member = await self.join_queue.get()
                
                # Verify they aren't a bot
                if member.bot:
                    self.join_queue.task_done()
                    continue

                # Verify member is still in guild
                if member.guild.get_member(member.id) is None:
                    self.join_queue.task_done()
                    continue

                if not self.welcoming_enabled:
                    log.info(f"Welcome disabled. Aborting interview for {member.name}.")
                    self.join_queue.task_done()
                    continue

                # Check Database if they already have a card
                try:
                    db = get_db()
                    res = db.table("user_profiles").select("has_library_card").eq("user_id", member.id).execute()
                    if res.data and res.data[0].get("has_library_card"):
                        log.info(f"User {member.name} already has a card, skipping.")
                        self.join_queue.task_done()
                        continue
                except Exception as e:
                    log.warning(f"DB check failed for {member.id}, proceeding anyway: {e}")

                await self._process_stage_one(member)
                
                self.join_queue.task_done()
                
                # Crucial sleep: pacing out server public messages and DM opens
                await asyncio.sleep(4.0) 
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in join_worker for Welcome Cog: {e}", exc_info=True)
                await asyncio.sleep(5) # Delay on error
                
    async def _process_stage_one(self, member: discord.Member):
        """Stage 1 + Initial Stage 2 DM Handshake"""
        # 1. Look for the public introductions channel
        intro_channel = discord.utils.get(member.guild.text_channels, name="introductions")
        
        if intro_channel:
            welcome_msg = (
                f"Welcome, {member.mention}. We are delighted to have you among us. "
                f"This community is a place for discovering, sharing, and connecting over knowledge. "
                f"We'd love to get to know you better — keep an eye on your direct messages."
            )
            try:
                await intro_channel.send(welcome_msg)
            except discord.Forbidden:
                log.warning(f"Lacked permissions to write to #introductions for {member.id}.")
        
        # 2. Initiate Stage 2: Slide into DMs
        initial_dm_msg = (
            f"Hey there, {member.name}! 👋 I'm Catalog, the resident librarian here.\n"
            f"I like to prepare personalized Library Cards for our new members so everyone knows who you are.\n\n"
            f"What do people call you around here? Any funny nicknames we should know about?"
        )
        
        try:
            dm_channel = await member.create_dm()
            await dm_channel.send(initial_dm_msg)
            
            # Setup conversation history in memory
            self.active_onboarding[member.id] = [
                types.Content(role="model", parts=[types.Part.from_text(text=initial_dm_msg)])
            ]
            log.info(f"Started Stage 2 DM onboarding for {member.name}.")
        except discord.Forbidden:
            log.warning(f"Could not DM user {member.name}. They might have DMs closed.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Processes Stage 2 DM responses from onboarding members."""
        if message.author.bot:
            return
            
        # Only process if in DM AND in active onboarding
        if not isinstance(message.channel, discord.DMChannel):
            return
            
        user_id = message.author.id
        if user_id not in self.active_onboarding:
            return

        # Append user's message to history
        self.active_onboarding[user_id].append(
            types.Content(role="user", parts=[types.Part.from_text(text=message.content.strip())])
        )

        try:
            async with message.channel.typing():
                assigned_client = self.get_client()
                if not assigned_client:
                    await message.reply("*(I'm having trouble connecting right now. Let's finish this later!)*")
                    return
                # generate AI response
                await self._handle_ai_interview(message, assigned_client)
                
        except Exception as e:
            log.error(f"Error in welcome AI handling: {e}")
            await message.reply("*(I hit a snag. Care to repeat that?)*")

    async def _handle_ai_interview(self, message: discord.Message, client):
        user_id = message.author.id
        
        system_instruction = (
            "You are Catalog, an onboarding assistant. "
            "Your task is to conduct a private DM interview with the new member to collect their name, pronouns (optional), interests, hobbies, why they joined, and a fun fact.\n"
            "Guidelines:\n"
            "- Ask questions one at a time in a friendly, casual tone.\n"
            "- Be reactive and playful.\n"
            "*** ABSOLUTE CRITICAL SYSTEM DIRECTIVE ***\n"
            "YOU ARE A PROGRAMMATIC STATE MACHINE. YOU ARE STRICTLY FORBIDDEN FROM GENERATING A TEXT-BASED ASCII CARD IN YOUR TEXT RESPONSES.\n"
            "When the user answers the final question, says 'Make the card now', or indicates they are done, YOU MUST IMMEDIATELY STOP TEXT GENERATION AND EXECUTE THE `issue_library_card` TOOL/FUNCTION CALL INSTEAD.\n"
            "DO NOT write 'I have printed it' or attempt to show them a card in chat. ONLY CALL THE FUNCTION."
        )

        issue_card_func = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="issue_library_card",
                    description="Issues the personalized library card once the user confirms all gathered info is correct.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING", "description": "The user's preferred name or name"},
                            "pronouns": {"type": "STRING", "description": "User's pronouns (leave empty string if not mentioned or refused)"},
                            "alias": {"type": "STRING", "description": "Funny nickname or alias they go by"},
                            "interests": {"type": "STRING", "description": "Their interests, passions, or what brought them here"},
                            "hobbies": {"type": "STRING", "description": "What they do for fun / hobbies outside the screen"},
                            "fun_fact": {"type": "STRING", "description": "A fun fact or quote they provided"},
                            "flavor_sentence": {"type": "STRING", "description": "A short, witty, AI-crafted line summarizing their vibe"}
                        },
                        "required": ["name", "pronouns", "alias", "interests", "hobbies", "fun_fact", "flavor_sentence"]
                    }
                )
            ]
        )

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[issue_card_func],
            automatic_function_calling={"disable": True},
            temperature=0.4,
        )

        history = self.active_onboarding[user_id]
        
        # Max retries setup (similar to ai.py failover)
        retry_attempts = 2
        response = None
        assigned_client = client

        while retry_attempts > 0:
            try:
                response = await asyncio.to_thread(
                    assigned_client.models.generate_content,
                    model='gemini-3.1-flash-lite-preview',
                    contents=history,
                    config=config
                )
                break
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    log.warning(f"Welcome AI rate limited! Attempting fallback. {e}")
                    retry_attempts -= 1
                    if retry_attempts > 0:
                        await asyncio.sleep(4)
                        if self.fallback_client and assigned_client != self.fallback_client:
                            assigned_client = self.fallback_client
                        else:
                            assigned_client = self.get_client()
                    else:
                        raise e
                else:
                    raise e
                    
        if not response:
            raise Exception("No response generated.")

        if hasattr(response, "function_calls") and response.function_calls:
            for call in response.function_calls:
                if call.name == "issue_library_card":
                    await self._process_stage_three(message.author, call.args)
                    return # End the flow

        # Normal text response
        reply_text = response.text
        if len(reply_text) > 2000:
            reply_text = reply_text[:1996] + "..."
            
        await message.reply(reply_text)
        
        # Add bot's response to history
        self.active_onboarding[user_id].append(
            types.Content(role="model", parts=[types.Part.from_text(text=reply_text)])
        )

    async def _process_stage_three(self, user: discord.User, args: dict):
        """Stage 3: Generate the card and post it to the showcase channel."""
        # 1. Let the user know in DMs that it's complete
        try:
            await user.send("Perfect! I'm minting your Library Card now... Come check it out in the server! 🎉")
        except discord.Forbidden:
            pass

        # 2. Extract arguments safely
        name = args.get("name", "Unknown Scribe")
        pronouns = args.get("pronouns", "").strip()
        if pronouns and pronouns.lower() not in ["none", "n/a", "no", "-", "unknown"]:
            name = f"{name} ({pronouns})"
            
        alias = args.get("alias", "The Mysterious")
        interests = args.get("interests", "Wandering the archives")
        hobbies = args.get("hobbies", "Reading dusty tomes")
        fun_fact = args.get("fun_fact", "Values knowledge over gold")
        flavor = args.get("flavor_sentence", "An enigmatic presence newly added to our records.")
        joined_date = datetime.now(timezone.utc).strftime("%b %d, %Y")

        # 3. ASCII Card string format
        card_content = (
            "```text\n"
            "╔══════════════════════════════════════════════════════╗\n"
            "║                 🎴 MEMBER LIBRARY CARD               ║\n"
            "╠══════════════════════════════════════════════════════╣\n"
            f"║  NAME:        {name[:38].ljust(38)} ║\n"
            f"║  ALIAS:       {alias[:38].ljust(38)} ║\n"
            f"║  INTERESTS:   {interests[:38].ljust(38)} ║\n"
            f"║  HOBBIES:     {hobbies[:38].ljust(38)} ║\n"
            f"║  FUN FACT:    {fun_fact[:38].ljust(38)} ║\n"
            f"║  JOINED:      {joined_date.ljust(38)} ║\n"
            "║  STATUS:      ✅ Officially One of Us                ║\n"
            "╚══════════════════════════════════════════════════════╝\n"
            "```\n"
            f"> *\"{flavor}\"*\n\n"
            f"Welcome to the archives, {user.mention}!\n"
            f"Love your card? Feel free to share it anywhere you like — you've officially been inducted. 🎉"
        )
        
        # 4. Post to the server
        for guild in self.bot.guilds:
            member = guild.get_member(user.id)
            if not member: 
                continue
                
            card_channel = guild.get_channel(1482736369024503808)
            if not card_channel:
                card_channel = discord.utils.get(guild.text_channels, name="library-cards")
            if not card_channel:
                log.warning(f"Could not find target channel in {guild.name}.")
                card_channel = discord.utils.get(guild.text_channels, name="introductions")
                
            if card_channel:
                try:
                    await card_channel.send(card_content)
                    log.info(f"Library card posted for {user.name} in {card_channel.name}.")
                except discord.Forbidden:
                    log.warning(f"Could not send library card to {card_channel.name} due to permissions.")
        
        # 5. Update Database to lock them from re-interviews
        try:
            db = get_db()
            db.table("user_profiles").upsert({
                "user_id": user.id,
                "has_library_card": True
            }).execute()
            log.info(f"Database updated: {user.name} now has a library card.")
        except Exception as e:
            log.error(f"Failed to update library card status in DB for {user.id}: {e}")

        # 6. Clean up the state
        if user.id in self.active_onboarding:
            del self.active_onboarding[user.id]

    @commands.command(name="initcard")
    @commands.has_permissions(administrator=True)
    async def initcard_cmd(self, ctx: commands.Context, *, target: str = None):
        """Force start the library card onboarding."""
        if not self.welcoming_enabled:
            await ctx.send("The welcoming system is currently disabled via the control panel.")
            return

        if not target:
            # Default to the caller themselves
            member = ctx.author
            await ctx.send(f"Initiating welcome sequence for {member.mention}...")
            await self._process_stage_one(member)
            return

        target_lower = target.lower()
        if target_lower in ["all", "everyone", "@everyone"]:
            await ctx.send("Queueing all eligible members without a library card... This will be safely delayed to prevent API limits.")
            
            queued_count = 0
            for member in ctx.guild.members:
                if member.bot:
                    continue
                    
                # Look up in DB
                try:
                    db = get_db()
                    res = db.table("user_profiles").select("has_library_card").eq("user_id", member.id).execute()
                    if res.data and res.data[0].get("has_library_card"):
                        continue # Skip users who already have it
                except Exception as e:
                    pass
                
                await self.join_queue.put(member)
                queued_count += 1
                
            await ctx.send(f"Successfully added {queued_count} members to the mass-onboarding queue. They will slowly get DMs over time.")
            return

        # Explicit user mention or ID
        try:
            member = await commands.MemberConverter().convert(ctx, target)
            await ctx.send(f"Initiating welcome sequence for {member.mention}...")
            await self._process_stage_one(member)
        except commands.MemberNotFound:
            await ctx.send(f"Could not find the member: `{target}`.")

async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))

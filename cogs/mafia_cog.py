import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import logging
from typing import Literal, Dict, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime

# --- PIP DEPENDENCIES ---
# discord.py>=2.3.0
# ------------------------

log = logging.getLogger(__name__)

# --- CONSTANTS & CONFIG ---

ROLE_CONFIG = {
    "Villager":   {"team": "town",    "emoji": "👤", "has_night_action": False, "desc": "Standard townie. Your only weapon is your vote."},
    "Detective":  {"team": "town",    "emoji": "🔍", "has_night_action": True,  "desc": "Investigate one player each night to find their team."},
    "Doctor":     {"team": "town",    "emoji": "💊", "has_night_action": True,  "desc": "Protect one player each night from being killed."},
    "Vigilante":  {"team": "town",    "emoji": "🔫", "has_night_action": True,  "desc": "Kill one player per game at night. Use it wisely."},
    "Mafia":      {"team": "mafia",   "emoji": "🔪", "has_night_action": True,  "desc": "Collaborate with other Mafia to kill one townie each night."},
    "Framer":     {"team": "mafia",   "emoji": "🎭", "has_night_action": True,  "desc": "Frame one player each night. If investigated, they appear as Mafia."},
    "Jester":     {"team": "neutral", "emoji": "🃏", "has_night_action": False, "desc": "Win by getting lynched by the town during the day."},
}

COLORS = {
    "lobby": 0x5865F2,    # Blurple
    "night": 0x2C2F33,    # Dark
    "day": 0xFAA61A,      # Gold
    "lynched": 0xED4245,  # Red
    "town_win": 0x57F287, # Green
    "mafia_win": 0xED4245,# Red
    "jester_win": 0xFEE75C # Yellow
}

# --- DATA MODELS ---

@dataclass
class Player:
    user_id: int
    display_name: str
    role: str = "Villager"
    alive: bool = True
    protected: bool = False
    framed: bool = False
    last_protected: Optional[int] = None
    used_vigilante_shot: bool = False
    
    @property
    def team(self) -> str:
        return ROLE_CONFIG[self.role]["team"]
    
    @property
    def emoji(self) -> str:
        return ROLE_CONFIG[self.role]["emoji"]

@dataclass
class GameSession:
    guild_id: int
    channel_id: int
    host_id: int
    players: Dict[int, Player] = field(default_factory=dict)
    phase: Literal["lobby", "night", "day", "ended"] = "lobby"
    round: int = 1
    night_actions: Dict[str, int] = field(default_factory=dict) # role -> target_id
    mafia_votes: Dict[int, int] = field(default_factory=dict) # voter_id -> target_id
    day_votes: Dict[int, int] = field(default_factory=dict)   # voter_id -> target_id
    jester_won_id: Optional[int] = None
    lobby_message_id: Optional[int] = None
    task: Optional[asyncio.Task] = None

# --- SESSION STORE ---
_sessions: Dict[int, GameSession] = {}

# --- UI VIEWS ---

class LobbyView(discord.ui.View):
    def __init__(self, cog: 'MafiaCog', guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Join Game", style=discord.ButtonStyle.success, custom_id="mafia_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = _sessions.get(self.guild_id)
        if not session or session.phase != "lobby":
            return await interaction.response.send_message("No lobby active.", ephemeral=True)
        
        if interaction.user.id in session.players:
            return await interaction.response.send_message("Already joined!", ephemeral=True)
        
        session.players[interaction.user.id] = Player(interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(f"Joined the game! ({len(session.players)} players)", ephemeral=True)
        await self.cog._update_lobby_embed(session)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, custom_id="mafia_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = _sessions.get(self.guild_id)
        if not session or session.phase != "lobby":
            return await interaction.response.send_message("No lobby active.", ephemeral=True)
        
        if interaction.user.id not in session.players:
            return await interaction.response.send_message("You're not in the lobby.", ephemeral=True)
        
        del session.players[interaction.user.id]
        await interaction.response.send_message("Left the lobby.", ephemeral=True)
        await self.cog._update_lobby_embed(session)

class NightActionView(discord.ui.View):
    def __init__(self, role: str, session: GameSession, player: Player, options: List[discord.SelectOption]):
        super().__init__(timeout=45)
        self.role = role
        self.session = session
        self.player = player
        self.target_id: Optional[int] = None

        if options:
            select = discord.ui.Select(placeholder=f"Choose your target...", options=options)
            select.callback = self.select_callback
            self.add_item(select)
        else:
            self.stop()

    async def select_callback(self, interaction: discord.Interaction):
        self.target_id = int(interaction.data['values'][0])

        # Disable all items before responding so the edit goes through cleanly
        for item in self.children:
            item.disabled = True

        if self.role == "Mafia":
            self.session.mafia_votes[self.player.user_id] = self.target_id
            await interaction.response.edit_message(
                content=f"✅ Voted to kill <@{self.target_id}>. Waiting for night to end...",
                view=self
            )
        else:
            self.session.night_actions[self.role] = self.target_id
            await interaction.response.edit_message(
                content=f"✅ Target selected: <@{self.target_id}>. Waiting for night to end...",
                view=self
            )
        self.stop()

class VoteView(discord.ui.View):
    def __init__(self, session: GameSession, options: List[discord.SelectOption]):
        super().__init__(timeout=60)
        self.session = session

        if options:
            select = discord.ui.Select(placeholder="Cast your lynch vote...", options=options)
            select.callback = self.select_callback
            self.add_item(select)
        else:
            self.stop()

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id not in self.session.players or not self.session.players[interaction.user.id].alive:
            return await interaction.response.send_message("Only alive players can vote.", ephemeral=True)
        
        target_id = int(interaction.data['values'][0])
        self.session.day_votes[interaction.user.id] = target_id
        await interaction.response.send_message(f"Voted for <@{target_id}>.", ephemeral=True)

class HelpView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.current_page = 0
        self.pages = [
            discord.Embed(title="🃏 Mafia — How to Play", description="Mafia is a social deduction game. **Town** wants to eliminate **Mafia**, and Mafia wants to outnumber Town.", color=COLORS['lobby'])
            .add_field(name="Cycles", value="The game alternates between **Night** (secret actions) and **Day** (public discussion & lynching).")
            .add_field(name="Winning", value="Town wins when all Mafia are dead. Mafia wins when they reach parity with Town."),
            
            discord.Embed(title="🌙 Night Phase", description="The city sleeps, but some are busy...", color=COLORS['night'])
            .add_field(name="Actions", value="Roles like Mafia, Doctor, and Detective perform secret actions via DM.")
            .add_field(name="Timeout", value="You have 45 seconds to submit your action. If you miss it, no action is taken."),
            
            discord.Embed(title="☀️ Day Phase", description="Morning reveals the night's events.", color=COLORS['day'])
            .add_field(name="Discussion", value="Discuss who might be Mafia. Use logic and claims to find the killers.")
            .add_field(name="Voting", value="Vote to lynch a player. Majority vote (>50%) is required for a lynch."),
            
            discord.Embed(title="🎭 Role Guide — Town", color=0x57F287)
            .add_field(name="Villager", value="No night action. Just survival and voting.", inline=False)
            .add_field(name="Detective", value="Investigates one player per night to learn their team.", inline=False)
            .add_field(name="Doctor", value="Protects one player per night from death. Cannot self-protect twice in a row.", inline=False)
            .add_field(name="Vigilante", value="Can kill one player once per game at night.", inline=False),
            
            discord.Embed(title="🔪 Role Guide — Mafia & Neutral", color=0xED4245)
            .add_field(name="Mafia", value="Chooses a target together to kill each night.", inline=False)
            .add_field(name="Framer", value="Frames a player so they appear as Mafia to the Detective.", inline=False)
            .add_field(name="Jester (Neutral)", value="Wins if they get lynched by the town. Watch out for suspicious behavior!", inline=False),
            
            discord.Embed(title="🏆 Commands", color=COLORS['lobby'])
            .add_field(name="Main Commands", value="`!mafia start` - Open lobby\n`!mafia begin` - Start game (Host)\n`!mafia status` - Current state\n`!mafia reset` - Force end")
        ]

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("Not your help menu.", ephemeral=True)
        self.current_page = (self.current_page - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.current_page])

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id: return await interaction.response.send_message("Not your help menu.", ephemeral=True)
        self.current_page = (self.current_page + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.current_page])

# --- MAIN COG ---

class MafiaCog(commands.Cog):
    """🃏 A fully-featured Mafia party game."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def cog_unload(self):
        for session in _sessions.values():
            if session.task: session.task.cancel()

    @commands.hybrid_group(name="mafia", fallback="help", description="Mafia game commands and rules.")
    async def mafia(self, ctx: commands.Context):
        """Show full rules and command guide."""
        view = HelpView(ctx.author.id)
        await ctx.send(embed=view.pages[0], view=view, ephemeral=True)

    @mafia.command(name="start", description="Open a new Mafia lobby.")
    async def mafia_start(self, ctx: commands.Context):
        if ctx.guild.id in _sessions:
            return await ctx.send("A game is already in progress or lobby is open.", ephemeral=True)
        
        session = GameSession(ctx.guild.id, ctx.channel.id, ctx.author.id)
        session.players[ctx.author.id] = Player(ctx.author.id, ctx.author.display_name)
        _sessions[ctx.guild.id] = session
        
        embed = self._make_lobby_embed(session)
        view = LobbyView(self, ctx.guild.id)
        msg = await ctx.send(embed=embed, view=view)
        session.lobby_message_id = msg.id

    @mafia.command(name="join", description="Join the current Mafia lobby.")
    async def mafia_join(self, ctx: commands.Context):
        session = _sessions.get(ctx.guild.id)
        if not session or session.phase != "lobby":
            return await ctx.send("No active lobby to join.", ephemeral=True)
        
        if ctx.author.id in session.players:
            return await ctx.send("You're already in!", ephemeral=True)
        
        session.players[ctx.author.id] = Player(ctx.author.id, ctx.author.display_name)
        await ctx.send("Joined the mafia lobby!", ephemeral=True)
        await self._update_lobby_embed(session)

    @mafia.command(name="begin", description="Start the game (Host only, 5+ players).")
    async def mafia_begin(self, ctx: commands.Context):
        session = _sessions.get(ctx.guild.id)
        if not session or session.phase != "lobby":
            return await ctx.send("No lobby active.", ephemeral=True)

        if ctx.author.id != session.host_id and not ctx.author.guild_permissions.administrator:
            return await ctx.send("Only the host can start the game.", ephemeral=True)

        if len(session.players) < 5:
            return await ctx.send(f"Need at least 5 players to start. (Current: {len(session.players)})")

        await self._assign_roles(session)

        # Notify roles via DM
        for p in session.players.values():
            await self._send_role_dm(p)

        await ctx.send("🎭 **Roles have been assigned! Check your DMs.** Game starting now...")
        # Game loop sets session.phase itself; don't set it here to avoid race
        session.task = asyncio.create_task(self._game_loop(session))

    @mafia.command(name="status", description="Show the current game status.")
    async def mafia_status(self, ctx: commands.Context):
        session = _sessions.get(ctx.guild.id)
        if not session:
            return await ctx.send("No game active in this server.")
        
        alive_txt = "\n".join([f"• {p.display_name}" for p in session.players.values() if p.alive])
        dead_txt = "\n".join([f"• ~~{p.display_name}~~ ({p.role})" for p in session.players.values() if not p.alive]) or "None"
        
        embed = discord.Embed(title=f"Mafia Status — Round {session.round}", color=COLORS.get(session.phase, 0x000000))
        embed.add_field(name="Phase", value=session.phase.capitalize())
        embed.add_field(name="Alive", value=alive_txt or "Nobody?", inline=False)
        embed.add_field(name="Dead", value=dead_txt, inline=False)
        await ctx.send(embed=embed)

    @mafia.command(name="reset", description="Force-end the current game (Host/Admin only).")
    async def mafia_reset(self, ctx: commands.Context):
        session = _sessions.get(ctx.guild.id)
        if not session:
            return await ctx.send("No game to reset.", ephemeral=True)
        
        if ctx.author.id != session.host_id and not ctx.author.guild_permissions.administrator:
            return await ctx.send("You don't have permission to reset the game.", ephemeral=True)
        
        if session.task: session.task.cancel()
        del _sessions[ctx.guild.id]
        await ctx.send("🛑 **Game has been force-reset.**")

    # --- INTERNAL HELPERS ---

    async def _update_lobby_embed(self, session: GameSession):
        if not session.lobby_message_id:
            return
        channel = self.bot.get_channel(session.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(session.lobby_message_id)
            await msg.edit(embed=self._make_lobby_embed(session))
        except Exception:
            pass

    def _make_lobby_embed(self, session: GameSession) -> discord.Embed:
        p_list = "\n".join([f"• {p.display_name}" for p in session.players.values()])
        embed = discord.Embed(title="Mafia Game Lobby", description=f"Host: <@{session.host_id}>\n\n**Players ({len(session.players)}):**\n{p_list or 'Waiting...'}", color=COLORS['lobby'])
        embed.set_footer(text="Host can type !mafia begin when 5+ players have joined.")
        return embed

    async def _assign_roles(self, session: GameSession):
        count = len(session.players)
        p_ids = list(session.players.keys())
        random.shuffle(p_ids)
        
        # Scaling logic
        if count <= 6:
            r_list = ["Mafia", "Detective", "Doctor"] + ["Villager"] * (count - 3)
        elif count <= 9:
            r_list = ["Mafia", "Mafia", "Detective", "Doctor", "Vigilante"] + ["Villager"] * (count - 5)
        else:
            r_list = ["Mafia", "Mafia", "Mafia", "Detective", "Doctor", "Vigilante", "Framer", "Jester"] + ["Villager"] * (count - 8)
        
        # Trim list if too many roles for some reason
        r_list = r_list[:count]
        random.shuffle(r_list)
        
        for i, pid in enumerate(p_ids):
            session.players[pid].role = r_list[i]

    async def _send_role_dm(self, player: Player):
        user = self.bot.get_user(player.user_id)
        if not user: return
        
        role_info = ROLE_CONFIG[player.role]
        embed = discord.Embed(title=f"Your Secret Role: {player.role}", description=role_info['desc'], color=COLORS['lobby'])
        embed.add_field(name="Team", value=role_info['team'].capitalize())
        embed.set_thumbnail(url="https://i.imgur.com/8fGQXmS.png") # Placeholder mafia icon
        try:
            await user.send(embed=embed)
        except: pass

    async def _game_loop(self, session: GameSession):
        while session.phase != "ended":
            # --- NIGHT PHASE ---
            await self._run_night_phase(session)
            
            # --- RESOLUTION ---
            deaths = await self._resolve_night_actions(session)
            
            # --- DAY PHASE ---
            await self._run_day_phase(session, deaths)
            
            if session.phase == "ended": break
            session.round += 1

    async def _run_night_phase(self, session: GameSession):
        session.phase = "night"
        session.night_actions.clear()
        session.mafia_votes.clear()
        
        embed = discord.Embed(title=f"🌙 Night {session.round} Falls...", description="The channel is now muted. Role holders, check your DMs for night actions!", color=COLORS['night'])
        await self._announce(session, embed)
        
        # Send Night Action DMs
        for p in session.players.values():
            if not p.alive or not ROLE_CONFIG[p.role]['has_night_action']: continue
            
            # Check Vigilante shot
            if p.role == "Vigilante" and p.used_vigilante_shot: continue
            
            user = self.bot.get_user(p.user_id)
            if not user: continue
            
            options = []
            for target in session.players.values():
                if not target.alive:
                    continue
                if target.user_id == p.user_id:
                    # Non-doctors can never target themselves
                    if p.role != "Doctor":
                        continue
                    # Doctor can't self-protect two nights in a row
                    if p.last_protected == p.user_id:
                        continue

                options.append(discord.SelectOption(label=target.display_name, value=str(target.user_id)))

            if not options:
                continue

            view = NightActionView(p.role, session, p, options)
            r_msg = f"Choose your night action for Night {session.round}:"
            try:
                await user.send(content=r_msg, view=view)
            except Exception:
                pass
            
        await asyncio.sleep(45)

    async def _resolve_night_actions(self, session: GameSession) -> List[Player]:
        # Reset flags
        for p in session.players.values():
            p.protected = False
            p.framed = False
        
        # 1. Framing
        framer_target = session.night_actions.get("Framer")
        if framer_target and framer_target in session.players:
            session.players[framer_target].framed = True
            
        # 2. Protection
        doc_target = session.night_actions.get("Doctor")
        if doc_target and doc_target in session.players:
            session.players[doc_target].protected = True
            # Track on the Doctor themselves (not the target) so the cooldown works correctly
            doc_player = next((p for p in session.players.values() if p.role == "Doctor"), None)
            if doc_player:
                doc_player.last_protected = doc_target
            
        # 3. Investigation (Immediate DM)
        det_target_id = session.night_actions.get("Detective")
        if det_target_id:
            det_p = next((p for p in session.players.values() if p.role == "Detective"), None)
            if det_p:
                target = session.players[det_target_id]
                result = "Mafia" if (target.team == "mafia" or target.framed) else "Innocent"
                user = self.bot.get_user(det_p.user_id)
                if user:
                    try: await user.send(f"🔍 **Investigation Result:** {target.display_name} is **{result}**.")
                    except: pass
        
        deaths = []
        
        # 4. Mafia Kill
        if session.mafia_votes:
            # Tally votes — majority wins, ties broken randomly
            v_counts: Dict[int, int] = {}
            for target_id in session.mafia_votes.values():
                v_counts[target_id] = v_counts.get(target_id, 0) + 1

            max_v = max(v_counts.values())
            candidates = [k for k, v in v_counts.items() if v == max_v]
            m_target_id = random.choice(candidates)

            # Guard: target must still be in the game and alive
            target = session.players.get(m_target_id)
            if target and target.alive and not target.protected:
                target.alive = False
                deaths.append(target)
                
        # 5. Vigilante Kill
        vi_target_id = session.night_actions.get("Vigilante")
        if vi_target_id:
            vi_p = next((p for p in session.players.values() if p.role == "Vigilante"), None)
            if vi_p:
                vi_p.used_vigilante_shot = True
                target = session.players[vi_target_id]
                if not target.protected:
                    if target not in deaths: # Don't kill twice
                        target.alive = False
                        deaths.append(target)
                        
        return deaths

    async def _run_day_phase(self, session: GameSession, night_deaths: List[Player]):
        session.phase = "day"
        session.day_votes.clear()
        
        death_txt = "\n".join([f"• {p.display_name} ({p.role})" for p in night_deaths]) or "No one died tonight."
        embed = discord.Embed(title=f"☀️ Day {session.round}", description=f"Morning arrives. Here are the events from last night:\n\n{death_txt}", color=COLORS['day'])
        await self._announce(session, embed)
        
        # Check win after night deaths
        winner = await self._check_win_condition(session)
        if winner:
            await self._end_game(session, winner)
            return

        # Discussion & Voting
        channel = self.bot.get_channel(session.channel_id)
        p_list = [
            discord.SelectOption(label=p.display_name, value=str(p.user_id))
            for p in session.players.values() if p.alive
        ]
        view = VoteView(session, p_list)
        vote_msg = await channel.send("🗳️ **Discussion phase (60s).** Use the menu below to cast your lynch vote.", view=view)

        await asyncio.sleep(60)

        # Disable the vote menu so no more votes can trickle in
        view.stop()
        for item in view.children:
            item.disabled = True
        try:
            await vote_msg.edit(view=view)
        except Exception:
            pass

        # Tally Day Votes
        if not session.day_votes:
            await channel.send("🕊️ No votes were cast. No one is lynched today.")
        else:
            v_counts: Dict[int, int] = {}
            for target_id in session.day_votes.values():
                v_counts[target_id] = v_counts.get(target_id, 0) + 1

            alive_count = len([p for p in session.players.values() if p.alive])
            threshold = alive_count // 2 + 1

            lynched_id = None
            for tid, cnt in v_counts.items():
                if cnt >= threshold:
                    lynched_id = tid
                    break

            if lynched_id:
                target = session.players[lynched_id]
                target.alive = False

                embed = discord.Embed(
                    title="⚖️ The Town has Spoken!",
                    description=f"**{target.display_name}** has been lynched. Their role was: **{target.role}**",
                    color=COLORS['lynched']
                )
                await self._announce(session, embed)

                if target.role == "Jester":
                    session.jester_won_id = target.user_id
                    await self._end_game(session, "jester")
                    return
            else:
                await channel.send("⚖️ No majority reached. No one is lynched today.")

        # Check win after lynch
        winner = await self._check_win_condition(session)
        if winner:
            await self._end_game(session, winner)

    async def _check_win_condition(self, session: GameSession) -> Optional[str]:
        alive_mafia = [p for p in session.players.values() if p.alive and p.team == "mafia"]
        alive_town = [p for p in session.players.values() if p.alive and p.team == "town"]
        
        if not alive_mafia:
            return "town"
        if len(alive_mafia) >= len(alive_town):
            return "mafia"
        return None

    async def _end_game(self, session: GameSession, winner: str):
        session.phase = "ended"
        
        if winner == "town":
            title, color = "🏆 Town Wins!", COLORS['town_win']
            desc = "All Mafia members have been eliminated."
        elif winner == "mafia":
            title, color = "🩸 Mafia Wins!", COLORS['mafia_win']
            desc = "The Mafia has successfully taken control."
        else:
            j_name = session.players[session.jester_won_id].display_name
            title, color = "🃏 Jester Wins!", COLORS['jester_win']
            desc = f"**{j_name}** has been lynched and wins the game!"

        embed = discord.Embed(title=title, description=desc, color=color)
        
        final_roles = "\n".join([f"• {p.display_name}: {p.role} ({'Alive' if p.alive else 'Dead'})" for p in session.players.values()])
        embed.add_field(name="Final Role Manifest", value=final_roles)
        
        await self._announce(session, embed)
        if session.guild_id in _sessions: del _sessions[session.guild_id]

    async def _announce(self, session: GameSession, embed: discord.Embed):
        channel = self.bot.get_channel(session.channel_id)
        if channel: await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        session = _sessions.get(member.guild.id)
        if session and member.id in session.players:
            p = session.players[member.id]
            if p.alive:
                p.alive = False
                channel = self.bot.get_channel(session.channel_id)
                if channel:
                    await channel.send(f"⚠️ **{member.display_name}** has left the server and is now dead.")
                # Win condition is checked at the next phase milestone to avoid
                # interrupting the game loop mid-sleep.

async def setup(bot: commands.Bot):
    await bot.add_cog(MafiaCog(bot))

import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import logging
import uuid
from typing import Literal, Dict, List, Optional, Union, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
import time
import math

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
    start_votes: Set[int] = field(default_factory=set)

# --- SESSION STORE ---
# These are kept at module level for shared access, but managed by the Cog
_sessions: Dict[int, GameSession] = {}
_web_sessions: Dict[str, int] = {} # UUID -> Guild ID
_rejoin_tokens: Dict[str, Dict[str, int]] = {} # session_id -> {token: user_id}
_ws_clients: Dict[str, set] = {} # session_id -> set of WebSocketResponse

# --- UI VIEWS ---

class LobbyView(discord.ui.View):
    def __init__(self, cog: 'MafiaCog', guild_id: int, session_id: Optional[str] = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        
        if session_id:
            url = self.cog._get_game_url(session_id)
            self.add_item(discord.ui.Button(label="Play in Browser", style=discord.ButtonStyle.link, url=url))

class NightActionView(discord.ui.View):
    def __init__(self, cog: 'MafiaCog', role: str, session: GameSession, player: Player, options: List[discord.SelectOption]):
        super().__init__(timeout=45)
        self.cog = cog
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
            # Broadcast is handled by Mafia kills being private, but we update vote counts
            session_id = next((k for k, v in _web_sessions.items() if v == self.session.guild_id), None)
            if session_id:
                counts = {}
                for tid in self.session.mafia_votes.values():
                    counts[tid] = counts.get(tid, 0) + 1
                asyncio.create_task(self.cog._broadcast_event(session_id, "vote_update", {"votes": counts, "type": "mafia"}))
        else:
            self.session.night_actions[self.role] = self.target_id
            await interaction.response.edit_message(
                content=f"✅ Target selected: <@{self.target_id}>. Waiting for night to end...",
                view=self
            )
        self.stop()

class VoteView(discord.ui.View):
    def __init__(self, cog: 'MafiaCog', session: GameSession, options: List[discord.SelectOption]):
        super().__init__(timeout=60)
        self.cog = cog
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
        
        session_id = next((k for k, v in _web_sessions.items() if v == self.session.guild_id), None)
        if session_id:
            counts = {}
            for tid in self.session.day_votes.values():
                counts[tid] = counts.get(tid, 0) + 1
            asyncio.create_task(self.cog._broadcast_event(session_id, "vote_update", {"votes": counts, "type": "day"}))

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

    async def cog_load(self):
        """Reload active sessions from SQLite on restart."""
        import sqlite3
        from db.local_db import DB_PATH
        import time
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            now = int(time.time())
            cursor.execute("SELECT session_id, guild_id, state_json, rejoin_tokens_json FROM mafia_sessions_persistence WHERE expires_at > ?", (now,))
            rows = cursor.fetchall()
            conn.close()
            
            for session_id, guild_id, state_json, rejoin_tokens_json in rows:
                state = json.loads(state_json)
                tokens = json.loads(rejoin_tokens_json)
                
                session = self._session_from_dict(state)
                _sessions[guild_id] = session
                _web_sessions[session_id] = guild_id
                _rejoin_tokens[session_id] = tokens
                
                # Resume game loop if in progress
                if session.phase in ["night", "day"]:
                    session.task = asyncio.create_task(self._game_loop(session))
                    
            log.info(f"Restored {len(rows)} active mafia sessions from SQLite.")
        except Exception as e:
            log.error(f"Failed to restore mafia sessions: {e}")

    @commands.hybrid_group(name="mafia", fallback="help", description="Mafia game commands and rules.")
    async def mafia(self, ctx: commands.Context):
        """Show full rules and command guide."""
        view = HelpView(ctx.author.id)
        await ctx.send(embed=view.pages[0], view=view, ephemeral=True)

    @mafia.command(name="web", description="Generate a web link for the current game session.")
    @app_commands.guild_only()
    async def mafia_web(self, ctx: commands.Context):
        """Generate a unique URL for the browser-playable version of this session."""
        session = _sessions.get(ctx.guild.id)
        
        # Bug 8: Generate session_id FIRST
        session_id = next((k for k, v in _web_sessions.items() if v == ctx.guild.id), None)
        if not session_id:
            session_id = str(uuid.uuid4())
            _web_sessions[session_id] = ctx.guild.id
            _rejoin_tokens[session_id] = {}

        # Auto-create lobby if none exists
        if not session:
            session = GameSession(ctx.guild.id, ctx.channel.id, ctx.author.id)
            _sessions[ctx.guild.id] = session
            
            embed = self._make_lobby_embed(session)
            view = LobbyView(self, ctx.guild.id, session_id)
            msg = await ctx.send(embed=embed, view=view)
            session.lobby_message_id = msg.id
            self._persist_session(session_id)

        from db.local_db import get_config
        game_url = self._get_game_url(session_id)
        
        embed = discord.Embed(
            title="🃏 Mafia Web Link Generated",
            description=f"You can now play this session in your browser!\n\n**[Click here to Play]({game_url})**",
            color=0x5865F2
        )
        embed.add_field(name="Host", value=ctx.guild.get_member(session.host_id).display_name if ctx.guild.get_member(session.host_id) else "Unknown")
        embed.add_field(name="Server", value=ctx.guild.name)
        embed.set_footer(text="Unique session ID: " + session_id)
        
        await ctx.send(embed=embed, ephemeral=True if ctx.interaction else False)
        self._persist_session(session_id)
        await self._update_lobby_embed(session)

    @mafia.command(name="start", description="Open a new Mafia lobby.")
    async def mafia_start(self, ctx: commands.Context):
        if ctx.guild.id in _sessions:
            return await ctx.send("A game is already in progress or lobby is open.", ephemeral=True)
        
        session = GameSession(ctx.guild.id, ctx.channel.id, ctx.author.id)
        session.players[ctx.author.id] = Player(ctx.author.id, ctx.author.display_name)
        _sessions[ctx.guild.id] = session
        
        # Check for existing web session
        session_id = next((k for k, v in _web_sessions.items() if v == ctx.guild.id), None)
        
        embed = self._make_lobby_embed(session)
        view = LobbyView(self, ctx.guild.id, session_id)
        msg = await ctx.send(embed=embed, view=view)
        session.lobby_message_id = msg.id
        self._persist_session(session_id) if session_id else None

    @mafia.command(name="join", description="Join the current Mafia lobby.")
    async def mafia_join(self, ctx: commands.Context):
        session = _sessions.get(ctx.guild.id)
        if not session or session.phase != "lobby":
            return await ctx.send("No active lobby to join.", ephemeral=True)
        
        if ctx.author.id in session.players:
            return await ctx.send("You're already in!", ephemeral=True)
        
        if any(p.display_name.lower() == ctx.author.display_name.lower() for p in session.players.values()):
            return await ctx.send("A player with this name is already in the game!", ephemeral=True)
        
        session.players[ctx.author.id] = Player(ctx.author.id, ctx.author.display_name)
        await ctx.send("Joined the mafia lobby!", ephemeral=True)
        await self._update_lobby_embed(session)

    @mafia.command(name="begin", description="Start the game (Host only, 5+ players).")
    async def mafia_begin(self, ctx: commands.Context):
        session = _sessions.get(ctx.guild.id)
        if not session or session.phase != "lobby":
            return await ctx.send("No lobby active.", ephemeral=True)
        
        if len(session.players) < 5:
            return await ctx.send("You need at least 5 players to start.")
        
        # Bug 3: Set phase immediately before awaits
        session.phase = "starting"
        
        await self._assign_roles(session)
        for p in session.players.values():
            await self._send_role_dm(p)
        
        await ctx.send("🎭 **Roles have been assigned! Check your DMs.** Game starting now...")
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
        
        await self._force_end_session(ctx.guild.id)
        await ctx.send("🛑 **Game has been force-reset.**")

    async def _force_end_session(self, guild_id: int):
        """Internal helper to fully clean up a session."""
        session = _sessions.get(guild_id)
        if not session: return

        if session.task: session.task.cancel()
        
        session_id = next((k for k, v in _web_sessions.items() if v == guild_id), None)
        if session_id:
            if session_id in _web_sessions: del _web_sessions[session_id]
            if session_id in _rejoin_tokens: del _rejoin_tokens[session_id]
            # Delete from SQLite
            import sqlite3
            from db.local_db import DB_PATH
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM mafia_sessions_persistence WHERE session_id = ?", (session_id,))
                conn.commit()
                conn.close()
            except: pass

        if guild_id in _sessions: del _sessions[guild_id]

    async def _handle_leave(self, session: GameSession, player_id: int):
        """Handles a player leaving the lobby."""
        if session.phase != "lobby": return
        if player_id in session.players:
            del session.players[player_id]
        if player_id in session.start_votes:
            session.start_votes.remove(player_id)
            
        await self._update_lobby_embed(session)
        session_id = next((k for k, v in _web_sessions.items() if v == session.guild_id), None)
        if session_id:
            self._persist_session(session_id)
            # Notify web clients via full state update or generic broadcast
            # We'll just update the lobby count for now; full state poll handles the rest.
        
        if not session.players:
            # Optional: auto-cleanup empty lobbies? 
            pass

    async def _handle_start_vote(self, session: GameSession, player_id: int):
        """Handles a 'Vote to Start' from the web UI."""
        if session.phase != "lobby": return
        session.start_votes.add(player_id)
        
        count = len(session.players)
        votes = len(session.start_votes)
        threshold = math.ceil(count * 0.75)
        
        if votes >= threshold and count >= 5:
            session.phase = "starting"
            
            session_id = next((k for k, v in _web_sessions.items() if v == session.guild_id), None)
            if session_id:
                await self._broadcast_event(session_id, "chat_message", {
                    "sender_id": 0,
                    "nickname": "System",
                    "text": "🛡️ 75% Majority reached! Assigning roles and starting the game...",
                    "timestamp": int(asyncio.get_running_loop().time())
                })
            
            await self._assign_roles(session)
            for p in session.players.values():
                await self._send_role_dm(p)
                
            session.task = asyncio.create_task(self._game_loop(session))
        else:
            session_id = next((k for k, v in _web_sessions.items() if v == session.guild_id), None)
            if session_id:
                asyncio.create_task(self._broadcast_event(session_id, "vote_update", {
                    "type": "start_vote",
                    "count": votes,
                    "threshold": threshold
                }))
                self._persist_session(session_id)

    # --- INTERNAL HELPERS ---

    async def _update_lobby_embed(self, session: GameSession):
        if not session.lobby_message_id:
            return
        channel = self.bot.get_channel(session.channel_id)
        if not channel:
            return
        try:
            session_id = next((k for k, v in _web_sessions.items() if v == session.guild_id), None)
            msg = await channel.fetch_message(session.lobby_message_id)
            await msg.edit(embed=self._make_lobby_embed(session), view=LobbyView(self, session.guild_id, session_id))
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
        
        if count <= 6:
            r_list = ["Mafia", "Detective", "Doctor"] + ["Villager"] * (count - 3)
        elif count <= 9:
            r_list = ["Mafia", "Mafia", "Detective", "Doctor", "Vigilante"] + ["Villager"] * (count - 5)
        else:
            r_list = ["Mafia", "Mafia", "Mafia", "Detective", "Doctor", "Vigilante", "Framer", "Jester"] + ["Villager"] * (count - 8)
        
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
        embed.set_thumbnail(url="https://i.imgur.com/8fGQXmS.png")
        try:
            await user.send(embed=embed)
        except: pass

    async def _game_loop(self, session: GameSession):
        session_id = next((k for k, v in _web_sessions.items() if v == session.guild_id), None)
        while session.phase != "ended":
            # --- NIGHT PHASE ---
            await self._run_night_phase(session, session_id)
            
            # --- RESOLUTION ---
            deaths = await self._resolve_night_actions(session)
            
            if session_id:
                death_names = [p.display_name for p in deaths]
                summary = "No one died tonight." if not death_names else f"Died tonight: {', '.join(death_names)}"
                await self._broadcast_event(session_id, "night_result", {"summary": summary})
                for p in deaths:
                    await self._broadcast_event(session_id, "player_died", {
                        "player_id": p.user_id,
                        "nickname": p.display_name,
                        "role_revealed": p.role,
                        "cause": "Night Action"
                    })

            # --- DAY PHASE ---
            await self._run_day_phase(session, deaths)
            
            if session.phase == "ended": break
            session.round += 1

    async def _run_night_phase(self, session: GameSession, session_id: str):
        """Runs the night logic."""
        # Bug 2: Only clear if not resuming a mid-night state
        if not session.night_actions and not session.mafia_votes:
            session.night_actions.clear()
            session.mafia_votes.clear()
            
        session.phase = "night"
        
        embed = discord.Embed(title=f"🌙 Night {session.round} Falls...", description="The channel is now muted. Role holders, check your DMs for night actions!", color=COLORS['night'])
        await self._announce(session, embed)
        
        for p in session.players.values():
            if not p.alive or not ROLE_CONFIG[p.role]['has_night_action']: continue
            if p.role == "Vigilante" and p.used_vigilante_shot: continue
            
            user = self.bot.get_user(p.user_id)
            if not user: continue
            
            options = []
            for target in session.players.values():
                if not target.alive: continue
                if target.user_id == p.user_id:
                    if p.role != "Doctor": continue
                    if p.last_protected == p.user_id: continue
                options.append(discord.SelectOption(label=target.display_name, value=str(target.user_id)))

            if not options: continue

            view = NightActionView(self, p.role, session, p, options)
            try:
                await user.send(content=f"Choose your night action for Night {session.round}:", view=view)
            except Exception: pass
            
        if session_id:
            await self._broadcast_event(session_id, "phase_change", {
                "phase": "night",
                "round": session.round,
                "message": "Night falls. Role actions are active."
            })
            self._persist_session(session_id)

        await asyncio.sleep(45)

    async def _resolve_night_actions(self, session: GameSession) -> List[Player]:
        for p in session.players.values():
            p.protected = False
            p.framed = False
        
        framer_target = session.night_actions.get("Framer")
        if framer_target and framer_target in session.players:
            session.players[framer_target].framed = True
            
        doc_target = session.night_actions.get("Doctor")
        if doc_target and doc_target in session.players:
            session.players[doc_target].protected = True
            doc_player = next((p for p in session.players.values() if p.role == "Doctor"), None)
            if doc_player: doc_player.last_protected = doc_target
            
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
        if session.mafia_votes:
            v_counts: Dict[int, int] = {}
            for target_id in session.mafia_votes.values():
                v_counts[target_id] = v_counts.get(target_id, 0) + 1
            max_v = max(v_counts.values())
            candidates = [k for k, v in v_counts.items() if v == max_v]
            m_target_id = random.choice(candidates)
            target = session.players.get(m_target_id)
            if target and target.alive and not target.protected:
                target.alive = False
                deaths.append(target)
                
        vi_target_id = session.night_actions.get("Vigilante")
        if vi_target_id:
            vi_p = next((p for p in session.players.values() if p.role == "Vigilante"), None)
            target = session.players.get(vi_target_id)
            if vi_p and target:
                # Bug 4: Harden Vigilante
                vi_p.used_vigilante_shot = True
                if target.alive and not target.protected and target not in deaths:
                    target.alive = False
                    deaths.append(target)
                        
        return deaths

    async def _run_day_phase(self, session: GameSession, night_deaths: List[Player]):
        session.phase = "day"
        session.day_votes.clear()
        
        death_txt = "\n".join([f"• {p.display_name} ({p.role})" for p in night_deaths]) or "No one died tonight."
        embed = discord.Embed(title=f"☀️ Day {session.round}", description=f"Morning arrives. Here are the events from last night:\n\n{death_txt}", color=COLORS['day'])
        await self._announce(session, embed)
        
        session_id = next((k for k, v in _web_sessions.items() if v == session.guild_id), None)
        if session_id:
            await self._broadcast_event(session_id, "phase_change", {
                "phase": "day",
                "round": session.round,
                "message": f"Morning reveals the night's events.\n{death_txt}"
            })
            self._persist_session(session_id)

        winner, reason = self._check_winner(session)
        if winner:
            await self._end_game(session, winner)
            return

        channel = self.bot.get_channel(session.channel_id)
        p_list = [discord.SelectOption(label=p.display_name, value=str(p.user_id)) for p in session.players.values() if p.alive]
        view = VoteView(self, session, p_list)
        vote_msg = await channel.send("🗳️ **Discussion phase (60s).** Use the menu below to cast your lynch vote.", view=view)
        await asyncio.sleep(60)
        view.stop()
        for item in view.children: item.disabled = True
        try: await vote_msg.edit(view=view)
        except Exception: pass

        v_counts: Dict[int, int] = {}
        for target_id in session.day_votes.values():
            v_counts[target_id] = v_counts.get(target_id, 0) + 1

        alive_count = len([p for p in session.players.values() if p.alive])
        threshold = alive_count // 2 + 1

        # Bug 6: Resolution by actual count, not insertion order
        lynched_id = None
        if v_counts:
            max_voted = max(v_counts, key=lambda tid: v_counts[tid])
            if v_counts[max_voted] >= threshold:
                lynched_id = max_voted
        
        if lynched_id:
            target = session.players[lynched_id]
            target.alive = False
            embed = discord.Embed(title="⚖️ The Town has Spoken!", description=f"**{target.display_name}** has been lynched. Their role was: **{target.role}**", color=COLORS['lynched'])
            await self._announce(session, embed)
            if session_id:
                await self._broadcast_event(session_id, "player_died", {"player_id": target.user_id, "nickname": target.display_name, "role_revealed": target.role, "cause": "Lynched"})
                self._persist_session(session_id)
            if target.role == "Jester":
                session.jester_won_id = target.user_id
                await self._end_game(session, "jester")
                return
        else:
            await channel.send("⚖️ No majority reached. No one is lynched today.")

        winner, reason = self._check_winner(session)
        if winner: await self._end_game(session, winner)

    def _check_winner(self, session: GameSession) -> Tuple[Optional[str], str]:
        """Calculates win conditions."""
        alive = [p for p in session.players.values() if p.alive]
        mafia = [p for p in alive if p.team == "mafia"]
        
        if not mafia:
            return "town", "All Mafia members have been eliminated!"
        
        # Bug 5: Parity against ALL non-mafia
        non_mafia = [p for p in alive if p.team != "mafia"]
        if len(mafia) >= len(non_mafia):
            return "mafia", "Mafia has reached parity and taken control of the town!"
            
        return None, ""

    async def _end_game(self, session: GameSession, winner: str):
        session.phase = "ended"
        if winner == "town": title, color = "🏆 Town Wins!", COLORS['town_win']; desc = "All Mafia members have been eliminated."
        elif winner == "mafia": title, color = "🩸 Mafia Wins!", COLORS['mafia_win']; desc = "The Mafia has successfully taken control."
        else: j_name = session.players[session.jester_won_id].display_name; title, color = "🃏 Jester Wins!", COLORS['jester_win']; desc = f"**{j_name}** has been lynched and wins the game!"
        
        session_id = next((k for k, v in _web_sessions.items() if v == session.guild_id), None)
        if session_id:
            await self._broadcast_event(session_id, "game_over", {"winner": winner, "reason": desc, "roles": {p.user_id: p.role for p in session.players.values()}})
            self._persist_session(session_id)
            _web_sessions.pop(session_id, None); _rejoin_tokens.pop(session_id, None)

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
                if channel: await channel.send(f"⚠️ **{member.display_name}** has left the server and is now dead.")
                session_id = next((k for k, v in _web_sessions.items() if v == member.guild.id), None)
                if session_id:
                    self._persist_session(session_id)
                    await self._broadcast_event(session_id, "player_died", {"player_id": member.id, "nickname": member.display_name, "role_revealed": p.role, "cause": "Left the server"})

    async def _broadcast_event(self, session_id: str, event_type: str, data: dict):
        clients = _ws_clients.get(session_id, set())
        if not clients: return
        payload = json.dumps({"type": event_type, "data": data})
        to_remove = set()
        for ws in clients:
            try: await ws.send_str(payload)
            except: to_remove.add(ws)
        for ws in to_remove: clients.remove(ws)

    def _persist_session(self, session_id: str):
        guild_id = _web_sessions.get(session_id)
        if not guild_id: return
        session = _sessions.get(guild_id)
        if not session: return
        state = self._session_to_dict(session)
        tokens = _rejoin_tokens.get(session_id, {})
        state_json = json.dumps(state)
        tokens_json = json.dumps(tokens)
        phase = session.phase
        async def _do_save():
            import sqlite3
            from db.local_db import DB_PATH
            import time as _time
            try:
                now = int(_time.time())
                expires = now + (2 * 60 * 60) if phase == "ended" else now + (24 * 60 * 60)
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO mafia_sessions_persistence 
                    (session_id, guild_id, state_json, rejoin_tokens_json, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (session_id, guild_id, state_json, tokens_json, now, expires))
                conn.commit()
                conn.close()
            except Exception as e:
                log.error(f"Failed to persist mafia session {session_id}: {e}")
        asyncio.create_task(_do_save())

    def _session_to_dict(self, session: GameSession) -> dict:
        """Serializes GameSession to a JSON-safe dict (excludes transient fields)."""
        return {
            "guild_id": session.guild_id,
            "channel_id": session.channel_id,
            "host_id": session.host_id,
            "players": {str(k): asdict(v) for k, v in session.players.items()},
            "phase": session.phase,
            "round": session.round,
            "night_actions": session.night_actions,
            "mafia_votes": {str(k): v for k, v in session.mafia_votes.items()},
            "day_votes": {str(k): v for k, v in session.day_votes.items()},
            "jester_won_id": session.jester_won_id,
            "start_votes": list(session.start_votes),
            "lobby_message_id": session.lobby_message_id
        }

    def _session_from_dict(self, data: dict) -> GameSession:
        """Restores GameSession from dict."""
        players = {}
        for pid, pdata in data.get("players", {}).items():
            players[int(pid)] = Player(**pdata)
        
        return GameSession(
            guild_id=data["guild_id"],
            channel_id=data["channel_id"],
            host_id=data["host_id"],
            players=players,
            phase=data["phase"],
            round=data["round"],
            night_actions=data.get("night_actions", {}),
            mafia_votes={int(k): v for k, v in data.get("mafia_votes", {}).items()},
            day_votes={int(k): v for k, v in data.get("day_votes", {}).items()},
            jester_won_id=data.get("jester_won_id"),
            start_votes=set(data.get("start_votes", [])),
            lobby_message_id=data.get("lobby_message_id")
        )

    def _get_game_url(self, session_id: str) -> str:
        """Constructs the game URL based on base_url and optional web_port."""
        from db.local_db import get_config
        base_url = get_config("base_url", "http://localhost").rstrip("/")
        web_port = get_config("mafia_web_port", "").strip()
        
        if web_port and web_port not in ["80", "443"]:
            # Check if base_url already has a port
            if ":" not in base_url.replace("://", ""):
                base_url = f"{base_url}:{web_port}"
        
        return f"{base_url}/mafia/{session_id}"

async def setup(bot: commands.Bot):
    await bot.add_cog(MafiaCog(bot))

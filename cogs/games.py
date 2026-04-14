import logging
import random
import asyncio
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import get_db
from utils import info_embed, error_embed

log = logging.getLogger(__name__)

# --- Minesweeper View ---

class MinesweeperButton(discord.ui.Button):
    def __init__(self, x: int, y: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=y)
        self.x = x
        self.y = y

    async def callback(self, interaction: discord.Interaction):
        view: 'MinesweeperView' = self.view
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        
        await view.reveal(self.x, self.y, interaction)

class MinesweeperView(discord.ui.View):
    def __init__(self, user_id: int, size: int, mines_count: int, difficulty: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.size = size
        self.mines_count = mines_count
        self.difficulty = difficulty
        self.start_time = datetime.now(timezone.utc)
        
        # Initialize board
        self.board = [[0 for _ in range(size)] for _ in range(size)]
        self.mines = set()
        self.revealed = set()
        self.game_over = False
        
        # Place mines
        while len(self.mines) < mines_count:
            mx, my = random.randint(0, size-1), random.randint(0, size-1)
            if (mx, my) not in self.mines:
                self.mines.add((mx, my))
                self.board[my][mx] = -1
        
        # Calculate numbers
        for my in range(size):
            for mx in range(size):
                if self.board[my][mx] == -1: continue
                count = 0
                for dy in [-1, 0, 1]:
                    for dx in [-1, 0, 1]:
                        if dx == 0 and dy == 0: continue
                        nx, ny = mx + dx, my + dy
                        if 0 <= nx < size and 0 <= ny < size and self.board[ny][nx] == -1:
                            count += 1
                self.board[my][mx] = count

        # Add buttons
        self.buttons = {}
        for y in range(size):
            for x in range(size):
                btn = MinesweeperButton(x, y)
                self.add_item(btn)
                self.buttons[(x, y)] = btn

    async def reveal(self, x: int, y: int, interaction: discord.Interaction):
        if (x, y) in self.revealed or self.game_over: return
        
        if (x, y) in self.mines:
            await self.end_game(False, interaction)
            return
            
        self._recursive_reveal(x, y)
        
        # Check win
        if len(self.revealed) == (self.size * self.size) - self.mines_count:
            await self.end_game(True, interaction)
        else:
            await interaction.response.edit_message(view=self)

    def _recursive_reveal(self, x: int, y: int):
        if (x, y) in self.revealed or (x, y) in self.mines: return
        
        self.revealed.add((x, y))
        btn = self.buttons[(x, y)]
        val = self.board[y][x]
        
        btn.label = str(val) if val > 0 else " "
        btn.style = discord.ButtonStyle.primary
        btn.disabled = True
        
        if val == 0:
            for dy in [-1, 0, 1]:
                for dx in [-1, 0, 1]:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.size and 0 <= ny < self.size:
                        self._recursive_reveal(nx, ny)

    async def end_game(self, won: bool, interaction: discord.Interaction):
        self.game_over = True
        duration = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        
        for (mx, my) in self.mines:
            btn = self.buttons[(mx, my)]
            btn.label = "💣"
            btn.style = discord.ButtonStyle.danger
            
        for btn in self.children:
            btn.disabled = True
            
        score = 0
        if won:
            base_points = {"easy": 100, "medium": 300, "hard": 600}
            time_bonus = max(1.0, 2.0 - (duration / 300)) # Faster is better, min 1x
            score = int(base_points.get(self.difficulty, 100) * time_bonus)
            
        # Save to DB
        try:
            db = get_db()
            db.table("minesweeper_sessions").insert({
                "user_id": self.user_id,
                "difficulty": self.difficulty,
                "score": score,
                "won": won,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e:
            log.error(f"Failed to save minesweeper score: {e}")

        msg = f"🎉 **You Won!** Score: {score}" if won else "💥 **BOOM!** Game Over."
        embed = discord.Embed(title="Minesweeper", description=msg, color=discord.Color.green() if won else discord.Color.red())
        embed.set_footer(text=f"Time: {int(duration)}s | Difficulty: {self.difficulty.capitalize()}")
        
        await interaction.response.edit_message(content=None, embed=embed, view=self)
        self.stop()

# --- Roulette Logic ---

class RouletteJoinView(discord.ui.View):
    def __init__(self, host_id: int, bet: int):
        super().__init__(timeout=60)
        self.host_id = host_id
        self.bet = bet
        self.players = {host_id}
        self.started = False

    @discord.ui.button(label="Join Game", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.players:
            return await interaction.response.send_message("You're already in!", ephemeral=True)
        
        # Check XP (Optional but recommended)
        try:
            db = get_db()
            res = db.table("xp_profiles").select("xp").eq("user_id", interaction.user.id).execute()
            if not res.data or res.data[0]['xp'] < self.bet:
                return await interaction.response.send_message(f"You don't have enough XP ({self.bet} required)!", ephemeral=True)
        except: pass

        self.players.add(interaction.user.id)
        await interaction.response.send_message(f"Joined! Players: {len(self.players)}", ephemeral=True)

    @discord.ui.button(label="Start Now", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.host_id:
            return await interaction.response.send_message("Only the host can start early.", ephemeral=True)
        if len(self.players) < 2:
            return await interaction.response.send_message("Need at least 2 players.", ephemeral=True)
        
        self.started = True
        self.stop()

# --- Main Cog ---

class Games(commands.Cog):
    """🎮 Multiplayer minigames and leaderboards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="minesweeper", description="Start a game of Minesweeper")
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="Easy (5x5, 5 mines)", value="easy"),
        app_commands.Choice(name="Medium (7x7, 12 mines)", value="medium"),
        app_commands.Choice(name="Hard (9x9, 20 mines)", value="hard")
    ])
    async def minesweeper(self, interaction: discord.Interaction, difficulty: str = "easy"):
        configs = {
            "easy": (5, 5),
            "medium": (7, 12),
            "hard": (9, 20)
        }
        size, mines = configs[difficulty]
        view = MinesweeperView(interaction.user.id, size, mines, difficulty)
        await interaction.response.send_message(f"Minesweeper - {difficulty.capitalize()}", view=view)

    @app_commands.command(name="roulette", description="Start a Russian Roulette lobby")
    async def roulette(self, interaction: discord.Interaction, bet_xp: int = 50):
        if bet_xp < 0: return await interaction.response.send_message("Invalid bet.", ephemeral=True)
        
        view = RouletteJoinView(interaction.user.id, bet_xp)
        embed = discord.Embed(
            title="Russian Roulette Lobby",
            description=f"Host: {interaction.user.mention}\nBet: **{bet_xp} XP**\n\nClick below to join! Game starts in 60s.",
            color=discord.Color.dark_red()
        )
        await interaction.response.send_message(embed=embed, view=view)
        
        await view.wait()
        
        if len(view.players) < 2:
            await interaction.edit_original_response(content="Not enough players joined.", embed=None, view=None)
            return

        await self._run_roulette(interaction, view.players, bet_xp)

    async def _run_roulette(self, interaction: discord.Interaction, player_ids: set, bet: int):
        players = []
        for pid in player_ids:
            user = self.bot.get_user(pid) or await self.bot.fetch_user(pid)
            if user: players.append(user)
        
        # Deduct bets
        db = get_db()
        for p in players:
            try:
                # This is a bit racey but fine for MVP
                res = db.table("xp_profiles").select("xp").eq("user_id", p.id).execute()
                if res.data:
                    db.table("xp_profiles").update({"xp": res.data[0]['xp'] - bet}).eq("user_id", p.id).execute()
            except: pass

        xp_pool = len(players) * bet
        survivors = list(players)
        round_num = 1
        
        # Log to DB
        session_id = str(uuid.uuid4())
        try:
             db.table("roulette_sessions").insert({
                 "session_id": session_id,
                 "host_user_id": interaction.user.id,
                 "players": [p.id for p in players],
                 "xp_pool": xp_pool
             }).execute()
        except: pass

        while len(survivors) > 1:
            await interaction.followup.send(f"**Round {round_num}**: The cylinder spins...")
            await asyncio.sleep(2)
            
            # One person is out
            loser = random.choice(survivors)
            survivors.remove(loser)
            
            await interaction.followup.send(f"💥 **BANG!** {loser.mention} is out.")
            await asyncio.sleep(1.5)
            round_num += 1

        winner = survivors[0]
        xp_won = xp_pool
        
        # Update winner XP
        try:
            res = db.table("xp_profiles").select("xp").eq("user_id", winner.id).execute()
            if res.data:
                db.table("xp_profiles").update({"xp": res.data[0]['xp'] + xp_won}).eq("user_id", winner.id).execute()
            
            db.table("roulette_sessions").update({
                "winner_user_ids": [winner.id],
                "xp_won_each": xp_won,
                "ended_at": datetime.now(timezone.utc).isoformat()
            }).eq("session_id", session_id).execute()
        except: pass

        embed = discord.Embed(
            title="Roulette Results",
            description=f"🏆 **{winner.mention}** is the sole survivor!\nThey won the pot of **{xp_pool} XP**.",
            color=discord.Color.gold()
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="topgames", description="View game leaderboards")
    @app_commands.choices(game=[
        app_commands.Choice(name="Minesweeper", value="minesweeper"),
        app_commands.Choice(name="Russian Roulette", value="roulette")
    ])
    async def topgames(self, interaction: discord.Interaction, game: str):
        db = get_db()
        embed = discord.Embed(title=f"{game.capitalize()} Leaderboard", color=discord.Color.blue())
        
        if game == "minesweeper":
            # Top by total score
            res = db.rpc('get_minesweeper_leaderboard').execute() # We'll need a SQL view or just query
            # For now, let's just query normally
            res = db.table("minesweeper_sessions").select("user_id, score").eq("won", True).order("score", desc=True).limit(10).execute()
            data = res.data
        else:
            # Top by XP won
            res = db.table("roulette_sessions").select("winner_user_ids, xp_won_each").order("xp_won_each", desc=True).limit(10).execute()
            data = []
            for row in res.data:
                winners = row.get("winner_user_ids", [])
                if winners: data.append({"user_id": winners[0], "score": row["xp_won_each"]})

        if not data:
            embed.description = "No entries yet."
        else:
            lines = []
            for i, row in enumerate(data, 1):
                user = self.bot.get_user(row['user_id']) or f"User {row['user_id']}"
                lines.append(f"{i}. **{user}**: {row['score']} pts/XP")
            embed.description = "\n".join(lines)
            
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))

import os
import json
import logging
import discord
from discord.ext import commands

log = logging.getLogger(__name__)

RULES_FILE = "db/rules.json"

DEFAULT_RULES = """**Respect the Silence (and each other):** Maintain a civil atmosphere. Harassment, hate speech, and bullying are strictly forbidden within these halls.
**No Vandalism:** Do not spam the text channels or flood the voice channels with excessive noise or disruptive behavior.
**Appropriate Attire:** Ensure all content (avatars, names, and media) remains appropriate for all scholars (Keep it PG-13/SFW unless in designated "Forbidden Sections").
**The Archivist's Word:** The Moderators (Archivists) have the final say in all disputes. If an Archivist asks you to stop a behavior, please comply.
**Categorize Your Research:** Use the correct channels for their intended purpose. Don't post memes in the "Research Hall" or serious debates in the "Common Room."
**Cite Your Sources:** If you are sharing art or writing that isn't yours, provide proper credit to the original creator.
**Spoiler Seals:** Use spoiler tags (||text||) when discussing plot points of new books, games, or anime to avoid ruining the story for other readers.
**No Dark Magic (Illegal Content):** Do not share links to pirated material, malware, or any content that violates Discord's Terms of Service.
**Solicitation & Alchemy:** No unauthorized advertising or "get rich quick" schemes. Self-promotion should be kept to the designated "Author's Corner."
**Protect Your Identity:** Do not share personal information (doxing) about yourself or other scholars.
**Sentient Oversight:** By interacting within these halls, you acknowledge that Catalog monitors and archives interactions to maintain the library's history and functionality.
**Permanent Records:** Data collected—including logs and research notes—is woven into the permanent fabric of the Archives. This information is intended to remain even if a scholar departs.
**The Right to Redaction (Legal Note):** While the Archives are designed for permanence, we honor the "Right to be Forgotten." If you require a manual deletion of your personal data to comply with privacy laws (GDPR/CCPA), please submit a Redaction Request to the engineer."""

class Rules(commands.Cog, name="Rules"):
    def __init__(self, bot):
        self.bot = bot
        self.channel_id = 1482736366990262283
        self.message_id = None
        self.rules_text = DEFAULT_RULES
        self._load()

    def _load(self):
        if os.path.exists(RULES_FILE):
            try:
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.rules_text = data.get("rules_text", self.rules_text)
                    self.channel_id = data.get("channel_id", self.channel_id)
                    self.message_id = data.get("message_id", self.message_id)
            except Exception as e:
                log.error(f"Failed to load rules: {e}")

    def _save(self):
        os.makedirs("db", exist_ok=True)
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "rules_text": self.rules_text,
                "channel_id": self.channel_id,
                "message_id": self.message_id
            }, f, indent=4)

    def get_rules_text(self):
        return self.rules_text

    async def update_rules_text(self, new_text: str):
        self.rules_text = new_text
        self._save()
        await self._sync_message()

    async def _sync_message(self):
        if not self.channel_id:
            return
            
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            log.warning(f"Rules cog cannot find channel {self.channel_id} to post rules embed.")
            return
            
        embed = discord.Embed(
            title="📜 The Library Between Worlds — Governance",
            description=self.rules_text,
            color=discord.Color.from_rgb(180, 160, 255)
        )
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="S.E.R.A. Automated Governance Override")
        
        try:
            if self.message_id:
                try:
                    msg = await channel.fetch_message(self.message_id)
                    await msg.edit(embed=embed)
                    log.info("Rules embed successfully updated via Web Panel!")
                    return
                except discord.NotFound:
                    log.warning("Saved rules message not found, creating a new one.")
            
            # Create new message
            msg = await channel.send(embed=embed)
            self.message_id = msg.id
            self._save()
        except Exception as e:
            log.error(f"Failed to sync rules message: {e}")

    @commands.group(name="postrules", invoke_without_command=True)
    async def postrules(self, ctx: commands.Context):
        """Forces the bot to post the rules embed in the current channel and binds to it for future web updates."""
        if not ctx.author.guild_permissions.administrator:
            return
            
        self.channel_id = ctx.channel.id
        self.message_id = None # Force a new message
        await self._sync_message()
        if hasattr(ctx, "message"):
            try:
                await ctx.message.delete()
            except:
                pass
        await ctx.send("Rules successfully initialized and bound to this channel.", delete_after=5)

async def setup(bot: commands.Bot):
    await bot.add_cog(Rules(bot))

import os
import re
import asyncio
import discord
from discord.ext import commands, tasks
import mylogger
import utils

logger = mylogger.getLogger(__name__)

API_ENDPOINT = "https://welcometothenhk.fandom.com/api.php"
WIKI_BASE = "https://welcometothenhk.fandom.com"
HEADERS = {"User-Agent": "WelcomeToTheNHK_DiscordBot/1.0 (Contact: ephemeral8997)"}
POLL_INTERVAL_SECONDS = 15

CHANNEL_ID = int(os.getenv("WIKI_RC_CHANNEL_ID", "0"))
WEBHOOK_NAME = os.getenv("WIKI_RC_WEBHOOK_NAME", "f/WelcomeToTheNHK")
HIDE_MINOR = os.getenv("WIKI_RC_HIDE_MINOR", "false").lower() in ("1", "true", "yes")
IGNORE_PAGES = {
    t.strip().replace("_", " ")
    for t in os.getenv("WIKI_RC_IGNORE_PAGES", "").split(",")
    if t.strip()
}


class Fandom(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_rcid = self.last_revid = None
        self.session_manager = utils.SessionManager()
        self.poll_changes.start()

    async def cog_unload(self):
        self.poll_changes.cancel()
        await self.session_manager.close()

    def _build_embed(self, change: dict) -> discord.Embed:
        revid, old_revid = change.get("revid"), change.get("old_revid")
        diff_url = f"{WIKI_BASE}/wiki/Special:Diff/{f'{old_revid}/{revid}' if old_revid else revid}"

        color = (
            discord.Color.red()
            if revid == 0
            else (
                discord.Color.green()
                if "new" in change
                else (
                    discord.Color.gold() if "minor" in change else discord.Color.blue()
                )
            )
        )

        embed = discord.Embed(
            title=change["title"],
            url=diff_url,
            description=utils.truncate(change.get("comment")),
            color=color,
            timestamp=discord.utils.parse_time(change["timestamp"]),
        )

        if "oldlen" in change and "newlen" in change:
            diff_val = change["newlen"] - change["oldlen"]
            embed.add_field(
                name="Size Change",
                value=f"{'+' if diff_val >= 0 else ''}{diff_val} bytes",
                inline=True,
            )

        return embed.set_footer(
            text=f"Edited by {change['user']} • rcid:{change['rcid']}"
        )

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def poll_changes(self):
        if not isinstance(
            channel := self.bot.get_channel(CHANNEL_ID), discord.TextChannel
        ):
            return

        params = {
            "action": "query",
            "list": "recentchanges",
            "rcdir": "newer",
            "format": "json",
            "rcprop": "ids|title|user|comment|timestamp|sizes|flags",
            "rclimit": "25",
        }
        if HIDE_MINOR:
            params["rcshow"] = "!minor"

        if not (
            data := await self.session_manager.fetch_json(
                API_ENDPOINT, headers=HEADERS, params=params
            )
        ):
            return

        changes = data.get("query", {}).get("recentchanges", [])

        if self.last_rcid is None and self.last_revid is None:
            if changes:
                self.last_rcid = max(c["rcid"] for c in changes)
                self.last_revid = max(c.get("revid", 0) for c in changes)
            return

        new_changes = [
            c
            for c in changes
            if c["rcid"] > (self.last_rcid or 0)
            and c.get("revid", 0) > (self.last_revid or 0)
        ]
        if not new_changes:
            return

        webhook = await utils.WebhookHelper.get_or_create(channel, WEBHOOK_NAME)

        for change in sorted(new_changes, key=lambda x: x["rcid"]):
            self.last_rcid = max(self.last_rcid or 0, change["rcid"])
            self.last_revid = max(self.last_revid or 0, change.get("revid", 0))

            if change["title"].replace("_", " ") not in IGNORE_PAGES:
                await webhook.send(embed=self._build_embed(change))

    async def page_exists(self, title: str) -> bool:
        res = await self.session_manager.fetch_json(
            API_ENDPOINT,
            headers=HEADERS,
            params={"action": "query", "titles": title, "format": "json"},
        )
        return res is not None and "-1" not in res.get("query", {}).get("pages", {})

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not (
            refs := list(
                dict.fromkeys(re.findall(r"\\[\[([^\[\\]]+)\]\]", message.content))
            )
        ):
            return

        checks = await asyncio.gather(
            *(self.page_exists(r.strip().replace(" ", "_")) for r in refs)
        )
        if valid := [
            f"• **{r}**: <{WIKI_BASE}/wiki/{r.strip().replace(' ', '_')}>"
            for r, exists in zip(refs, checks)
            if exists
        ]:
            await message.reply(
                "**📚 Wiki Pages Found:**\n" + "\n".join(valid), mention_author=False
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Fandom(bot))

import os
import re
import discord
from discord.ext import commands, tasks
import mylogger
import utils

logger = mylogger.getLogger(__name__)

# config
API_ENDPOINT = "https://welcometothenhk.fandom.com/api.php"
WIKI_BASE = "https://welcometothenhk.fandom.com"
WIKI_USER_AGENT = "WelcomeToTheNHK_DiscordBot/1.0 (Contact: ephemeral8997)"
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
        self.last_rcid = None
        self.last_revid = None
        self.session_manager = utils.SessionManager()
        self.poll_changes.start()

    async def cog_unload(self):
        self.poll_changes.cancel()
        await self.session_manager.close()

    def _build_embed(self, change: dict) -> discord.Embed:
        """Atomic builder for recent change discord embeds."""
        revid = change.get("revid")
        old_revid = change.get("old_revid")

        if old_revid:
            diff_url = f"{WIKI_BASE}/wiki/Special:Diff/{old_revid}/{revid}"
        else:
            diff_url = f"{WIKI_BASE}/wiki/Special:Diff/{revid}"

        is_minor = "minor" in change
        if revid == 0:
            color = discord.Color.red()
        elif "new" in change:
            color = discord.Color.green()
        elif is_minor:
            color = discord.Color.gold()
        else:
            color = discord.Color.blue()

        embed = discord.Embed(
            title=change["title"],
            url=diff_url,
            description=change.get("comment", "").strip() or None,
            color=color,
            timestamp=discord.utils.parse_time(change["timestamp"]),
        )

        if "oldlen" in change and "newlen" in change:
            diff_val = change["newlen"] - change["oldlen"]
            sign = "+" if diff_val >= 0 else ""
            embed.add_field(
                name="Size Change", value=f"{sign}{diff_val} bytes", inline=True
            )

        embed.set_footer(text=f"Edited by {change['user']} • rcid:{change['rcid']}")
        return embed

    async def _recover_state_from_channel(
        self, channel: discord.TextChannel
    ) -> tuple[int | None, int | None]:
        """scan last messages in the channel to recover last rcid and revid"""
        rcids = []
        revids = []
        try:
            async for message in channel.history(limit=50):
                for embed in message.embeds:
                    if embed.footer and embed.footer.text:
                        rcid_match = re.search(r"rcid:(\d+)", embed.footer.text)
                        if rcid_match:
                            rcids.append(int(rcid_match.group(1)))

                    if embed.url:
                        revid_match = re.search(
                            r"Special:Diff/(?:(\d+)/)?(\d+)", embed.url
                        )
                        if revid_match:
                            revids.append(int(revid_match.group(2)))
        except Exception as e:
            logger.error("Error scanning channel history: %s", str(e))

        max_rcid = max(rcids) if rcids else None
        max_revid = max(revids) if revids else None
        return max_rcid, max_revid

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def poll_changes(self):
        if CHANNEL_ID == 0:
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        params = {
            "action": "query",
            "list": "recentchanges",
            "rcprop": "ids|title|user|comment|timestamp|sizes|flags",
            "rclimit": "25",
            "rcdir": "newer",  # oldest to newest
            "format": "json",
        }

        if HIDE_MINOR:
            params["rcshow"] = "!minor"

        try:
            session = await self.session_manager.get_session()
            async with session.get(
                API_ENDPOINT, params=params, headers={"User-Agent": WIKI_USER_AGENT}
            ) as resp:
                if resp.status != 200:
                    logger.error("API returned status %s", resp.status)
                    return
                data = await resp.json()
        except Exception as e:
            logger.error("Error fetching recent changes: %s", str(e))
            return

        changes = data.get("query", {}).get("recentchanges", [])

        if self.last_rcid is None and self.last_revid is None:
            max_rcid, max_revid = await self._recover_state_from_channel(channel)
            if max_rcid is not None or max_revid is not None:
                self.last_rcid = max_rcid
                self.last_revid = max_revid
                logger.info(
                    "Recovered state from channel history: last_rcid=%s, last_revid=%s",
                    self.last_rcid,
                    self.last_revid,
                )
            elif changes:
                self.last_rcid = max(c["rcid"] for c in changes)
                self.last_revid = max(c.get("revid", 0) for c in changes)
                logger.info(
                    "Seeded initial state from API: last_rcid=%s, last_revid=%s",
                    self.last_rcid,
                    self.last_revid,
                )
                return

        if not changes:
            return

        def is_new_change(c: dict) -> bool:
            if self.last_rcid is not None and c["rcid"] <= self.last_rcid:
                return False
            if self.last_revid is not None and c.get("revid", 0) <= self.last_revid:
                return False
            return True

        new_changes = [c for c in changes if is_new_change(c)]
        if not new_changes:
            return

        webhook = await utils.WebhookHelper.get_or_create_webhook(channel, WEBHOOK_NAME)

        for change in sorted(new_changes, key=lambda x: x["rcid"]):
            self.last_rcid = max(self.last_rcid or 0, change["rcid"])
            if "revid" in change:
                self.last_revid = max(self.last_revid or 0, change["revid"])

            normalized_title = change["title"].replace("_", " ")
            if normalized_title in IGNORE_PAGES:
                continue

            embed = self._build_embed(change)
            await webhook.send(embed=embed)

    @poll_changes.before_loop
    async def before_fetch(self):
        await self.bot.wait_until_ready()

    async def page_exists(self, page_title: str) -> bool:
        params = {"action": "query", "titles": page_title, "format": "json"}
        try:
            session = await self.session_manager.get_session()
            async with session.get(
                API_ENDPOINT, params=params, headers={"User-Agent": WIKI_USER_AGENT}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pages = data.get("query", {}).get("pages", {})
                    return "-1" not in pages
        except Exception as e:
            logger.error("Error checking page existence: %s", str(e))
        return False

    @staticmethod
    def extract_references(content: str) -> list[str]:
        pattern = r"\[\[([^\[\]]+)\]\]"
        matches = re.findall(pattern, content)
        seen = set()
        return [
            ref for ref in matches if not (ref.lower() in seen or seen.add(ref.lower()))
        ]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        references = self.extract_references(message.content)
        if not references:
            return

        valid_links = []
        for ref in references:
            formatted_title = ref.strip().replace(" ", "_")
            if await self.page_exists(formatted_title):
                url = f"{WIKI_BASE}/wiki/{formatted_title}"
                valid_links.append(f"• **{ref}**: <{url}>")

        if valid_links:
            response = "**📚 Wiki Pages Found:**\n" + "\n".join(valid_links)
            await message.reply(response, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fandom(bot))

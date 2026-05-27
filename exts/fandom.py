import os
import discord
from discord.ext import commands, tasks
import mylogger
import re
import utils

logger = mylogger.getLogger(__name__)

# -------------------------- CONFIGURATION --------------------------
API_ENDPOINT = "https://welcometothenhk.fandom.com/api.php"
WIKI_BASE = "https://welcometothenhk.fandom.com"
WIKI_USER_AGENT = "WelcomeToTheNHK_DiscordBot/1.0 (Contact: ephemeral8997)"
POLL_INTERVAL_SECONDS = 15

CHANNEL_ID = int(os.getenv("WIKI_RC_CHANNEL_ID", "0"))
WEBHOOK_NAME = os.getenv("WIKI_RC_WEBHOOK_NAME", "f/WelcomeToTheNHK")

HIDE_MINOR = os.getenv("WIKI_RC_HIDE_MINOR", "false").lower() in ("1", "true", "yes")

IGNORE_PAGES = {
    t.strip().replace("_", " ").title()
    for t in os.getenv("WIKI_RC_IGNORE_PAGES", "").split(",")
    if t.strip()
}


class Fandom(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_rcid = None
        self.session_manager = utils.SessionManager()
        self.poll_changes.start()

    async def cog_unload(self):
        self.poll_changes.cancel()
        await self.session_manager.close()

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def poll_changes(self):
        if CHANNEL_ID == 0:
            logger.warning("CHANNEL_ID is not set — skipping wiki poll.")
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Could not find channel with ID %s", CHANNEL_ID)
            return

        params = {
            "action": "query",
            "list": "recentchanges",
            "rcprop": "ids|title|user|comment|timestamp|sizes|flags",
            "rclimit": "1",
            "format": "json",
        }

        headers = {"User-Agent": WIKI_USER_AGENT}

        try:
            session = await self.session_manager.get_session()
            async with session.get(
                API_ENDPOINT, params=params, headers=headers
            ) as resp:
                if resp.status != 200:
                    logger.error("API returned status %s", resp.status)
                    return
                data = await resp.json()
        except Exception as e:
            logger.error("Error fetching recent changes: %s", str(e))
            return

        changes = data.get("query", {}).get("recentchanges", [])
        if not changes:
            return

        change = changes[0]
        is_minor = "minor" in change

        if HIDE_MINOR and is_minor:
            return

        page_title = change["title"].replace("_", " ").title()
        if page_title in IGNORE_PAGES:
            logger.debug("Ignored edit to %s (in ignore list)", change["title"])
            return

        current_rcid = change["rcid"]

        if self.last_rcid is None:
            self.last_rcid = current_rcid
            return

        if current_rcid <= self.last_rcid:
            return

        self.last_rcid = current_rcid

        # build diff URL
        revid = change.get("revid")
        old_revid = change.get("old_revid")
        if old_revid:
            diff_url = f"{WIKI_BASE}/wiki/Special:Diff/{revid}/{old_revid}"
        else:
            diff_url = f"{WIKI_BASE}/wiki/Special:Diff/{revid}"

        size_diff = None
        if "oldlen" in change and "newlen" in change:
            diff_val = change["newlen"] - change["oldlen"]
            sign = "+" if diff_val >= 0 else ""
            size_diff = f"{sign}{diff_val} bytes"

        if revid == 0:
            color = discord.Color.red()
        elif "new" in change:
            color = discord.Color.green()
        elif is_minor:
            color = discord.Color.gold()
        else:
            color = discord.Color.blue()

        description = change.get("comment", "").strip() or None

        embed = discord.Embed(
            title=change["title"],
            url=diff_url,
            description=description,
            color=color,
            timestamp=discord.utils.parse_time(change["timestamp"]),
        )

        if size_diff:
            embed.add_field(name="Size Change", value=size_diff, inline=True)

        embed.set_footer(text=f"Edited by {change['user']}")

        webhook = await utils.WebhookHelper.get_or_create_webhook(channel, WEBHOOK_NAME)
        await webhook.send(embed=embed)

    @poll_changes.before_loop
    async def before_fetch(self):
        await self.bot.wait_until_ready()

    async def page_exists(self, page_title: str) -> bool:
        params = {"action": "query", "titles": page_title, "format": "json"}

        try:
            session = await self.session_manager.get_session()
            async with session.get(API_ENDPOINT, params=params) as response:
                if response.status == 200:
                    data = await response.json()
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
        unique_refs = []
        for ref in matches:
            lower_ref = ref.lower()
            if lower_ref not in seen:
                seen.add(lower_ref)
                unique_refs.append(ref)
        return unique_refs

    @staticmethod
    def format_page_title(title: str) -> str:
        return title.strip().replace(" ", "_")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        references = self.extract_references(message.content)
        if not references:
            return

        valid_links = []
        for ref in references:
            formatted_title = self.format_page_title(ref)
            if await self.page_exists(formatted_title):
                url = f"{WIKI_BASE}/wiki/{formatted_title}"
                valid_links.append(f"• **{ref}**: <{url}>")

        if valid_links:
            response = "**📚 Wiki Pages Found:**\n" + "\n".join(valid_links)
            await message.reply(response, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fandom(bot))

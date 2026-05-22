import os
import discord
from discord.ext import commands, tasks
import mylogger
import re
import utils

logger = mylogger.getLogger(__name__)

API_ENDPOINT = "https://welcometothenhk.fandom.com/api.php"
WIKI_BASE = "https://welcometothenhk.fandom.com"
WIKI_USER_AGENT = "WelcomeToTheNHK_DiscordBot/1.0 (Contact: ephemeral8997)"
POLL_INTERVAL_SECONDS = 15

CHANNEL_ID = int(os.getenv("WIKI_RC_CHANNEL_ID", "0"))
WEBHOOK_NAME = os.getenv("WIKI_RC_WEBHOOK_NAME", "f/WelcomeToTheNHK")

HIDE_MINOR = os.getenv("WIKI_RC_HIDE_MINOR", "false").lower() in ("1", "true", "yes")
HIDE_BOTS = os.getenv("WIKI_RC_HIDE_BOTS", "false").lower() in ("1", "true", "yes")

IGNORE_PAGES = {
    t.strip().replace("_", " ").title()
    for t in os.getenv("WIKI_RC_IGNORE_PAGES", "").split(",")
    if t.strip()
}


def _build_rcshow() -> str | None:
    filters = []
    if HIDE_MINOR:
        filters.append("!minor")
    if HIDE_BOTS:
        filters.append("!bot")
    return "|".join(filters) if filters else None


# mediawiki actions
LOG_ACTION_LABELS: dict[tuple[str, str], str] = {
    ("delete", "delete"): "🗑️ Page deleted",
    ("delete", "restore"): "♻️ Page restored",
    ("move", "move"): "➡️ Page moved",
    ("move", "move_redir"): "➡️ Page moved (over redirect)",
    ("protect", "protect"): "🔒 Page protected",
    ("protect", "modify"): "🔒 Protection modified",
    ("protect", "unprotect"): "🔓 Page unprotected",
    ("block", "block"): "🚫 User blocked",
    ("block", "unblock"): "✅ User unblocked",
    ("upload", "upload"): "📁 File uploaded",
    ("upload", "overwrite"): "📁 File re-uploaded",
    ("rights", "rights"): "🛡️ User rights changed",
    ("import", "import"): "📥 Page imported",
}


class Fandom(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_rcid: int | None = None
        self.session_manager = utils.SessionManager()
        self.poll_changes.start()

    async def cog_unload(self):
        self.poll_changes.cancel()
        await self.session_manager.close()

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def poll_changes(self):
        if CHANNEL_ID == 0:
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if channel is None:
            return

        params: dict[str, str] = {
            "action": "query",
            "list": "recentchanges",
            # ids        – rcid, revid, old_revid
            # title      – page title
            # user       – editor username / IP
            # comment    – edit summary
            # timestamp  – ISO 8601 timestamp
            # sizes      – oldlen / newlen for byte-diff
            # flags      – minor / bot / new / redirect
            # tags       – applied edit tags (e.g. "Visual edit", "Reverted")
            # loginfo    – log type & action (deletions, moves, protections …)
            # redirect   – whether the page is a redirect
            "rcprop": "ids|title|user|comment|timestamp|sizes|flags|tags|loginfo|redirect",
            # 5 changes per poll
            "rclimit": "5",
            # oldest first
            "rcdir": "newer",
            "format": "json",
        }

        rcshow = _build_rcshow()
        if rcshow:
            params["rcshow"] = rcshow

        headers = {"User-Agent": WIKI_USER_AGENT}

        try:
            session = await self.session_manager.get_session()
            async with session.get(
                API_ENDPOINT, params=params, headers=headers
            ) as resp:
                if resp.status != 200:
                    logger.warning("Wiki API returned HTTP %s", resp.status)
                    return
                data = await resp.json()
        except Exception as exc:
            logger.exception("Error fetching recent changes: %s", exc)
            return

        changes: list[dict] = data.get("query", {}).get("recentchanges", [])
        if not changes:
            return

        if self.last_rcid is None:
            self.last_rcid = changes[-1]["rcid"]  # newest (rcdir=newer → last item)
            return

        new_changes = [c for c in changes if c["rcid"] > self.last_rcid]
        if not new_changes:
            return

        # a crash shouldn't repost
        self.last_rcid = new_changes[-1]["rcid"]

        webhook = await utils.WebhookHelper.get_or_create_webhook(channel, WEBHOOK_NAME)  # type: ignore

        for change in new_changes:
            embed = self._build_embed(change)
            if embed is None:
                continue
            await webhook.send(embed=embed)

    @poll_changes.before_loop
    async def before_fetch(self):
        await self.bot.wait_until_ready()

    def _build_embed(self, change: dict) -> discord.Embed | None:
        title: str = change.get("title", "")

        # respect IGNORE_PAGES
        if title.replace("_", " ").title() in IGNORE_PAGES:
            logger.debug("Ignored edit to %s (WIKI_RC_IGNORE_PAGES)", title)
            return None

        is_minor = "minor" in change
        is_bot = "bot" in change
        is_new = "new" in change
        is_redirect = "redirect" in change
        is_log = change.get("type") == "log"

        revid = change.get("revid", 0)
        old_revid = change.get("old_revid", 0)

        if is_log:
            page_slug = title.replace(" ", "_")
            url = f"{WIKI_BASE}/wiki/Special:Log?page={page_slug}"
        elif old_revid:
            url = f"{WIKI_BASE}/wiki/Special:Diff/{old_revid}/{revid}"
        else:
            url = f"{WIKI_BASE}/wiki/Special:Diff/{revid}"

        size_diff = ""
        if "oldlen" in change and "newlen" in change:
            diff_val = change["newlen"] - change["oldlen"]
            sign = "+" if diff_val >= 0 else ""
            size_diff = f"{sign}{diff_val} bytes"

        if is_log:
            color = discord.Color.purple()
        elif revid == 0:
            color = discord.Color.red()
        elif is_new:
            color = discord.Color.green()
        elif is_minor:
            color = discord.Color.gold()
        else:
            color = discord.Color.blue()

        display_title = title
        if is_redirect:
            display_title = f"↪ {title} (redirect)"

        summary = change.get("comment") or "(no summary)"

        log_label: str | None = None
        if is_log:
            log_type = change.get("logtype", "")
            log_action = change.get("logaction", "")
            log_label = LOG_ACTION_LABELS.get(
                (log_type, log_action),
                f"{log_type}/{log_action}",
            )

        tags: list[str] = change.get("tags", [])

        embed = discord.Embed(
            title=display_title,
            url=url,
            description=summary,
            color=color,
            timestamp=discord.utils.parse_time(change["timestamp"]),
        )

        author_name = change.get("user", "Unknown")
        if is_bot:
            author_name = f"🤖 {author_name}"
        embed.set_author(name=author_name)

        if log_label:
            embed.add_field(name="Log Action", value=log_label, inline=True)

        if size_diff:
            embed.add_field(name="Size Change", value=size_diff, inline=True)

        if not is_log:
            embed.add_field(name="Revision ID", value=str(revid), inline=True)

        if tags:
            embed.add_field(name="Tags", value=", ".join(tags), inline=False)

        embed.set_footer(text=f"rcid:{change['rcid']}")
        return embed

    async def page_exists(self, page_title: str) -> bool:
        params = {"action": "query", "titles": page_title, "format": "json"}
        try:
            session = await self.session_manager.get_session()
            async with session.get(API_ENDPOINT, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    pages = data.get("query", {}).get("pages", {})
                    return "-1" not in pages
        except Exception:
            pass
        return False

    def extract_references(self, content: str) -> list[str]:
        matches = re.findall(r"\[\[([^\[\]]+)\]\]", content)
        seen: set[str] = set()
        unique: list[str] = []
        for match in matches:
            key = match.lower()
            if key not in seen:
                seen.add(key)
                unique.append(match)
        return unique

    def format_page_title(self, title: str) -> str:
        return title.strip().replace(" ", "_")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        references = self.extract_references(message.content)
        if not references:
            return

        valid_links: list[str] = []
        for ref in references:
            formatted = self.format_page_title(ref)
            if await self.page_exists(formatted):
                url = f"{WIKI_BASE}/wiki/{formatted}"
                valid_links.append(f"• **{ref}**: <{url}>")

        if valid_links:
            response = "**Wiki Pages Found:**\n" + "\n".join(valid_links)
            await message.reply(response, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fandom(bot))

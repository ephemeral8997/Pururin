import os
import re
import discord
from discord.ext import commands, tasks
import mylogger
import utils

logger = mylogger.getLogger(__name__)

REDDIT_URL = "https://www.reddit.com/r/WelcomeToTheNHK/new.json?limit=1"
HEADERS = {
    "User-Agent": "DiscordBot:com.yourcompany.NHKFeed:v1.0 (by /u/ephemeral8997)"
}
FLAIR_PATTERN = re.compile(r"^(?:(:\w+:)\s*)? (.+)$")


class WelcomeNHKFeed(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.channel_id = int(os.getenv("REDDIT_WELCOME_CHANNEL_ID", 0))
        self.last_post_id = None
        self.webhook_name = os.getenv("REDDIT_WEBHOOK_NAME", "r/WelcomeToTheNHK")
        self.session_manager = utils.SessionManager()
        self.fetch_reddit_posts.start()

    async def cog_unload(self) -> None:
        self.fetch_reddit_posts.cancel()
        await self.session_manager.close()

    @tasks.loop(minutes=10)
    async def fetch_reddit_posts(self):
        if not self.channel_id or not (
            data := await self.session_manager.fetch_json(
                REDDIT_URL, headers=HEADERS
            )
        ):
            return

        try:
            post = data["data"]["children"][0]["data"]
        except (KeyError, IndexError):
            return

        if post.get("id") == self.last_post_id or not isinstance(
            channel := self.bot.get_channel(self.channel_id), discord.TextChannel
        ):
            return
        self.last_post_id = post.get("id")

        flags = f"{'🔞 ' if post.get('over_18') else ''}{'📌 ' if post.get('stickied') else ''}"
        embed = discord.Embed(
            title=f"{flags}{post.get('title', 'No Title')}",
            url=f"https://reddit.com{post.get('permalink', '')}",
            description=utils.truncate(post.get("selftext")),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        ).set_thumbnail(
            url="https://www.redditstatic.com/desktop2x/img/favicon/apple-icon-57x57.png"
        )

        if raw_flair := post.get("link_flair_text"):
            if m := FLAIR_PATTERN.match(raw_flair):
                embed.add_field(name="Flair", value=m.group(2).strip(), inline=True)

        embed.add_field(name="Score", value=str(post.get("score", 0)), inline=True)
        embed.add_field(
            name="Comments", value=str(post.get("num_comments", 0)), inline=True
        )
        utils.apply_embed_media(
            embed, post.get("url_overridden_by_dest", ""), post.get("thumbnail", "")
        )

        if crosspost := post.get("crosspost_parent_list"):
            origin = crosspost[0]
            embed.add_field(
                name="Crossposted from",
                value=f"r/{origin.get('subreddit', 'unknown')} by u/{origin.get('author', 'unknown')}",
                inline=False,
            )

        if not post.get("is_self", True) and (
            ext_url := post.get("url", "")
        ).startswith("http"):
            embed.add_field(name="Link", value=f"[View]({ext_url})", inline=True)

        embed.set_footer(text=f"u/{post.get('author', 'unknown')}")

        try:
            webhook = await utils.WebhookHelper.get_or_create(
                channel, self.webhook_name
            )
            if await utils.WebhookHelper.should_post(channel, webhook.id, embed.url):
                await webhook.send(embed=embed, username=self.webhook_name)
        except Exception as e:
            logger.error(f"Error sending webhook: {e}")

    @fetch_reddit_posts.before_loop
    async def before_fetch(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeNHKFeed(bot))

import os
import re
import discord
from discord.ext import commands, tasks
import mylogger
import utils

logger = mylogger.getLogger(__name__)

REDDIT_URL = "https://www.reddit.com/r/WelcomeToTheNHK/new.json?limit=1"
REDDIT_USER_AGENT = "DiscordBot:com.yourcompany.NHKFeed:v1.0 (by /u/ephemeral8997)"

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
        if not self.channel_id:
            logger.warning("No channel ID configured; skipping fetch.")
            return

        headers = {"User-Agent": REDDIT_USER_AGENT}

        # fetch Reddit API data
        try:
            session = await self.session_manager.get_session()
            async with session.get(REDDIT_URL, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Reddit API returned status {resp.status}")
                    return
                data = await resp.json()
        except Exception as e:
            logger.error(f"Error fetching Reddit data: {str(e)}")
            return

        try:
            post_data = data["data"]["children"][0]["data"]
            post_id = post_data["id"]
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to parse post structure: {str(e)}")
            return

        if post_id == self.last_post_id:
            return
        self.last_post_id = post_id

        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.warning(f"Channel {self.channel_id} not found or inaccessible.")
            return

        flags = ""
        if post_data.get("over_18"):
            flags += "🔞 "
        if post_data.get("stickied"):
            flags += "📌 "

        title = f"{flags}{post_data.get('title', 'No Title')}"

        description = post_data.get("selftext", "").strip() or None

        embed = discord.Embed(
            title=title,
            url=f"https://reddit.com{post_data.get('permalink', '')}",
            description=utils.truncate_text(description) if description else None,
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )

        embed.set_thumbnail(
            url="https://www.redditstatic.com/desktop2x/img/favicon/apple-icon-57x57.png"
        )

        raw_flair = post_data.get("link_flair_text")
        if raw_flair:
            match = FLAIR_PATTERN.match(raw_flair)
            if match:
                _, fname = match.groups()
                embed.add_field(name="Flair", value=fname.strip(), inline=True)

        score = post_data.get("score", 0)
        comments = post_data.get("num_comments", 0)
        embed.add_field(name="Score", value=str(score), inline=True)
        embed.add_field(name="Comments", value=str(comments), inline=True)

        image_url = post_data.get("url_overridden_by_dest", "")
        if image_url and image_url.lower().endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp")
        ):
            embed.set_image(url=image_url)
        elif (
            post_data.get("thumbnail", "").startswith("http")
            and post_data.get("thumbnail") != "self"
        ):
            embed.set_image(url=post_data["thumbnail"])

        crosspost = post_data.get("crosspost_parent_list")
        if crosspost:
            origin = crosspost[0]
            embed.add_field(
                name="Crossposted from",
                value=f"r/{origin.get('subreddit', 'unknown')} by u/{origin.get('author', 'unknown')}",
                inline=False,
            )

        if not post_data.get("is_self", True):
            external_url = post_data.get("url", "")
            if external_url and external_url.startswith("http"):
                embed.add_field(
                    name="Link", value=f"[View]({external_url})", inline=True
                )

        embed.set_footer(text=f"u/{post_data.get('author', 'unknown')}")

        try:
            webhook = await utils.WebhookHelper.get_or_create_webhook(channel, self.webhook_name)  # type: ignore
            if not await utils.WebhookHelper.should_post_via_webhook(channel, webhook, embed):  # type: ignore
                return

            await webhook.send(embed=embed, username=self.webhook_name)
            logger.info(f"Posted new post to #{channel.name}")  # type: ignore

        except Exception as e:
            logger.error(f"Error sending webhook: {str(e)}")

    @fetch_reddit_posts.before_loop
    async def before_fetch(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeNHKFeed(bot))

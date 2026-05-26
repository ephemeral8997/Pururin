import os
import re
import discord
from discord.ext import commands, tasks
import aiohttp
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

        embed = discord.Embed(
            title=post_data.get("title", "No Title"),
            url=f"https://reddit.com{post_data.get('permalink', '')}",
            description=utils.truncate_text(
                post_data.get("selftext", "") or "No description provided."
            ),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )

        # Reddit icon as thumbnail
        embed.set_thumbnail(
            url="https://www.redditstatic.com/desktop2x/img/favicon/apple-icon-57x57.png"
        )

        raw_flair = post_data.get("link_flair_text")
        if raw_flair:
            match = FLAIR_PATTERN.match(raw_flair)
            if match:
                _, fname = match.groups()
                embed.add_field(name="Flair", value=fname.strip(), inline=True)

        # statistics
        embed.add_field(
            name="Votes",
            value=f"⬆️ {post_data.get('ups', 0)} | ⬇️ {post_data.get('downs', 0)} | Score: {post_data.get('score', 0)}",
            inline=False,
        )
        embed.add_field(
            name="Comments", value=str(post_data.get("num_comments", 0)), inline=True
        )
        embed.add_field(
            name="Flags",
            value=f"NSFW: {'🔞 Yes' if post_data.get('over_18') else '✅ No'} | Stickied: {'📌 Yes' if post_data.get('stickied') else '❌ No'}",
            inline=False,
        )

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
                value=f"r/{origin.get('subreddit', 'unknown')} • u/{origin.get('author', 'unknown')}",
                inline=False,
            )

        if not post_data.get("is_self", True):
            external_url = post_data.get("url", "")
            if external_url:
                embed.set_author(
                    name="🔗 External Link",
                    url=external_url,
                    icon_url="https://cdn-icons-png.flaticon.com/16/1046/1046490.png",
                )

        author_name = post_data.get("author", "unknown")
        embed.set_footer(
            text=f"Posted by u/{author_name} • r/{post_data.get('subreddit', 'WelcomeToTheNHK')}",
            icon_url=post_data.get("author_icon_img", None),
        )

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

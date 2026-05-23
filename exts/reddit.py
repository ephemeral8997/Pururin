import os
import discord
from discord.ext import commands, tasks
import aiohttp
import mylogger
import utils

logger = mylogger.getLogger(__name__)

REDDIT_URL = "https://www.reddit.com/r/WelcomeToTheNHK/new.json?limit=1"
REDDIT_USER_AGENT = "DiscordBot:com.yourcompany.NHKFeed:v1.0 (by /u/ephemeral8997)"


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
            return

        headers = {"User-Agent": REDDIT_USER_AGENT}

        try:
            session = await self.session_manager.get_session()
            async with session.get(REDDIT_URL, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Reddit API returned status {resp.status}")
                    return
                data = await resp.json()
        except Exception as e:
            logger.error(f"Error fetching Reddit data: {e}")
            return

        try:
            post = data["data"]["children"][0]["data"]
            post_id = post["id"]
        except Exception as e:
            logger.error(f"Error parsing Reddit post: {e}")
            return

        if post_id == self.last_post_id:
            return

        self.last_post_id = post_id
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.warning(f"Channel {self.channel_id} not found")
            return

        embed = discord.Embed(
            title=post["title"],
            url=f"https://reddit.com{post['permalink']}",
            description=utils.truncate_text(post.get("selftext", "")),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )

        embed.set_thumbnail(
            url="https://www.redditstatic.com/desktop2x/img/favicon/apple-icon-57x57.png"
        )

        if flair := post.get("link_flair_text"):
            embed.add_field(name="Flair", value=flair, inline=True)

        embed.add_field(name="Upvotes", value=str(post.get("ups", 0)), inline=True)
        embed.add_field(
            name="Comments", value=str(post.get("num_comments", 0)), inline=True
        )

        if (img := post.get("url_overridden_by_dest", "")) and img.endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp")
        ):
            embed.set_image(url=img)

        if crosspost := post.get("crosspost_parent_list"):
            origin = crosspost[0].get("subreddit_name_prefixed", "Unknown")
            embed.add_field(name="Crossposted from", value=origin, inline=False)

        embed.set_footer(text=f"Posted by u/{post.get('author', 'unknown')}")

        try:
            webhook = await utils.WebhookHelper.get_or_create_webhook(
                channel, self.webhook_name  # type: ignore
            )
            if not await utils.WebhookHelper.should_post_via_webhook(
                channel, webhook, embed  # type: ignore
            ):
                return

            await webhook.send(embed=embed, username=self.webhook_name)
            logger.info(f"Posted new Reddit post to #{channel.name}")  # type: ignore
        except Exception as e:
            logger.error(f"Error sending webhook: {e}")

    @fetch_reddit_posts.before_loop
    async def before_fetch(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeNHKFeed(bot))

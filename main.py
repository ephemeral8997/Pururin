import os
import time
import discord
from discord.ext import commands, tasks
import mylogger
import utils

logger = mylogger.getLogger(__name__)

SUBREDDIT = "WelcomeToTheNHK"
REDDIT_BASE = f"https://www.reddit.com/r/{SUBREDDIT}"
REDDIT_USER_AGENT = "DiscordBot:NHKFeed:v1.0 (by /u/ephemeral8997)"

CHANNEL_ID = int(os.getenv("REDDIT_WELCOME_CHANNEL_ID", 0))
WEBHOOK_NAME = os.getenv("REDDIT_WEBHOOK_NAME", "r/WelcomeToTheNHK")

RISING_SCORE_THRESHOLD = int(os.getenv("REDDIT_RISING_SCORE", "50"))

SCORE_CHECK_INTERVAL = int(os.getenv("REDDIT_SCORE_CHECK_MINUTES", "30"))


def _build_embed(post: dict, *, alert: bool = False) -> discord.Embed:
    title = post.get("title", "(no title)")

    badges: list[str] = []
    if post.get("is_original_content"):
        badges.append("🆕 OC")
    if post.get("over_18"):
        badges.append("🔞 NSFW")
    if post.get("spoiler"):
        badges.append("⚠️ Spoiler")
    if post.get("is_video"):
        badges.append("🎬 Video")
    if badges:
        title = f"{title}  {'  '.join(badges)}"

    if alert:
        color = discord.Color.gold()
    elif post.get("over_18"):
        color = discord.Color.red()
    elif post.get("is_original_content"):
        color = discord.Color.green()
    else:
        color = discord.Color.orange()

    raw_text = post.get("selftext", "") or ""
    if raw_text in ("[removed]", "[deleted]"):
        raw_text = ""
    description = utils.truncate_text(raw_text) if raw_text else ""

    if alert:
        score = post.get("ups", 0)
        description = (
            f"🔥 **This post just crossed {score:,} upvotes!**\n\n{description}".strip()
        )

    embed = discord.Embed(
        title=title,
        url=f"https://reddit.com{post['permalink']}",
        description=description or None,
        color=color,
        timestamp=discord.utils.utcnow(),
    )

    ratio = post.get("upvote_ratio", 0)
    ratio_str = f"{ratio * 100:.0f}%" if ratio else "N/A"

    ups = post.get("ups", 0)
    embed.add_field(
        name="Upvotes",
        value=f"{ups:,}  ({ratio_str} upvoted)",
        inline=True,
    )

    embed.add_field(
        name="Comments",
        value=str(post.get("num_comments", 0)),
        inline=True,
    )

    if flair := post.get("link_flair_text"):
        embed.add_field(name="Flair", value=flair, inline=True)

    awards = post.get("total_awards_received", 0)
    if awards:
        embed.add_field(name="Awards", value=f"🏆 {awards}", inline=True)

    if post.get("edited"):
        embed.add_field(name="Edited", value="✏️ Yes", inline=True)

    img = post.get("url_overridden_by_dest", "")
    if img and img.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        embed.set_image(url=img)

    if crosspost := post.get("crosspost_parent_list"):
        origin = crosspost[0].get("subreddit_name_prefixed", "Unknown")
        embed.add_field(name="Crossposted from", value=origin, inline=False)

    embed.set_footer(text=f"Posted by u/{post.get('author', 'unknown')}")
    return embed


class WelcomeNHKRedFeed(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session_manager = utils.SessionManager()

        self._seen_ids: set[str] = set()

        # post_id -> {"score": int, "alerted": bool}
        self._tracked: dict[str, dict] = {}

        self.fetch_new_posts.start()
        self.check_rising_scores.start()

    async def cog_unload(self) -> None:
        self.fetch_new_posts.cancel()
        self.check_rising_scores.cancel()
        await self.session_manager.close()

    async def _get_channel_and_webhook(
        self,
    ) -> tuple[discord.TextChannel, discord.Webhook] | tuple[None, None]:
        if not CHANNEL_ID:
            return None, None

        channel = self.bot.get_channel(CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            logger.warning(
                "reddit feed: channel %s not found or not a text channel", CHANNEL_ID
            )
            return None, None

        webhook = await utils.WebhookHelper.get_or_create_webhook(channel, WEBHOOK_NAME)
        return channel, webhook

    async def _fetch_json(self, url: str) -> dict | None:
        headers = {"User-Agent": REDDIT_USER_AGENT}
        try:
            session = await self.session_manager.get_session()
            async with session.get(url, headers=headers) as resp:
                if resp.status == 429:
                    logger.warning("Reddit rate-limited us (429)")
                    return None
                if resp.status != 200:
                    logger.warning(
                        "Reddit API returned HTTP %s for %s", resp.status, url
                    )
                    return None
                return await resp.json()
        except Exception as exc:
            logger.exception("Error fetching %s: %s", url, exc)
            return None

    @tasks.loop(minutes=10)
    async def fetch_new_posts(self):
        channel, webhook = await self._get_channel_and_webhook()
        if not channel or not webhook:
            return

        url = f"{REDDIT_BASE}/new.json?limit=5"
        data = await self._fetch_json(url)
        if not data:
            return

        try:
            children = data["data"]["children"]
        except (KeyError, IndexError) as exc:
            logger.error("Unexpected Reddit JSON structure: %s", exc)
            return

        for child in reversed(children):
            post = child["data"]
            post_id = post.get("id")
            if not post_id or post_id in self._seen_ids:
                continue

            self._seen_ids.add(post_id)

            self._tracked[post_id] = {
                "post": post,
                "score": post.get("ups", 0),
                "alerted": False,
            }

            embed = _build_embed(post)
            try:
                await webhook.send(embed=embed, username=WEBHOOK_NAME)
                logger.info("Posted new Reddit post '%s' to #%s", post.get("title"), channel.name)  # type: ignore
            except Exception as exc:
                logger.error("Error sending new-post webhook: %s", exc)

    @fetch_new_posts.before_loop
    async def before_fetch_new(self):
        await self.bot.wait_until_ready()
        url = f"{REDDIT_BASE}/new.json?limit=25"
        data = await self._fetch_json(url)
        if not data:
            return
        try:
            for child in data["data"]["children"]:
                post = child["data"]
                post_id = post.get("id")
                if post_id:
                    self._seen_ids.add(post_id)
                    self._tracked[post_id] = {
                        "post": post,
                        "score": post.get("ups", 0),
                        "alerted": False,
                    }
        except (KeyError, IndexError) as exc:
            logger.error("Failed to seed seen IDs: %s", exc)

    @tasks.loop(minutes=SCORE_CHECK_INTERVAL)
    async def check_rising_scores(self):
        if not self._tracked:
            return

        channel, webhook = await self._get_channel_and_webhook()
        if not channel or not webhook:
            return

        for post_id, entry in list(self._tracked.items()):
            if entry["alerted"]:
                del self._tracked[post_id]
                continue

            permalink = entry["post"].get("permalink", "")
            if not permalink:
                continue

            data = await self._fetch_json(f"https://reddit.com{permalink}.json?limit=1")
            if not data or not isinstance(data, list):
                continue

            try:
                fresh_post = data[0]["data"]["children"][0]["data"]
            except (KeyError, IndexError):
                continue

            new_score = fresh_post.get("ups", 0)
            entry["score"] = new_score
            entry["post"] = fresh_post  # keep metadata current

            # drop very old posts
            age_hours = (time.time() - fresh_post.get("created_utc", 0)) / 3600
            if age_hours > 48:
                del self._tracked[post_id]
                continue

            if new_score >= RISING_SCORE_THRESHOLD:
                entry["alerted"] = True
                embed = _build_embed(fresh_post, alert=True)
                try:
                    await webhook.send(embed=embed, username=WEBHOOK_NAME)
                    logger.info(
                        "Rising alert: '%s' reached %s upvotes",
                        fresh_post.get("title"),
                        new_score,
                    )
                except Exception as exc:
                    logger.error("Error sending rising-score webhook: %s", exc)

    @check_rising_scores.before_loop
    async def before_check_rising(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeNHKRedFeed(bot))

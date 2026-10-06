import discord
import aiohttp
import mylogger

logger = mylogger.getLogger(__name__)


class SessionManager:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_json(self, url: str, **kwargs) -> dict | None:
        try:
            session = await self.get_session()
            async with session.get(url, **kwargs) as resp:
                return await resp.json() if resp.status == 200 else None
        except Exception as e:
            logger.error(f"HTTP GET error for {url}: {e}")
            return None


class WebhookHelper:

    @staticmethod
    async def get_or_create(channel: discord.TextChannel, name: str) -> discord.Webhook:
        return discord.utils.get(
            await channel.webhooks(), name=name
        ) or await channel.create_webhook(name=name)

    @staticmethod
    async def should_post(channel: discord.TextChannel, wh_id: int, url: str) -> bool:
        """Checks recent channel history to prevent duplicate webhook embeds."""
        async for msg in channel.history(limit=10):
            if msg.webhook_id == wh_id and msg.embeds and msg.embeds[0].url == url:
                return False
        return True


def truncate(text: str | None, limit: int = 500) -> str:
    if not (t := (text or "").strip()):
        return "*No description.*"
    if len(t) <= limit:
        return t
    truncated = t[:limit]
    return (truncated.rsplit(" ", 1)[0] if " " in truncated else truncated) + "..."


def apply_embed_media(
    embed: discord.Embed, image_url: str = "", thumb_url: str = ""
) -> None:
    if image_url.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        embed.set_image(url=image_url)
    elif thumb_url.startswith("http") and thumb_url != "self":
        embed.set_image(url=thumb_url)

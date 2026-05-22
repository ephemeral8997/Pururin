import discord
import aiohttp
import mylogger

logger = mylogger.getLogger(__name__)


class WebhookHelper:
    @staticmethod
    async def get_or_create_webhook(
        channel: discord.TextChannel, name: str
    ) -> discord.Webhook:
        webhooks: list[discord.Webhook] = await channel.webhooks()
        webhook: discord.Webhook | None = discord.utils.get(webhooks, name=name)
        if not webhook:
            webhook = await channel.create_webhook(name=name)
            logger.info("Created webhook '%s' in #%s", name, channel.name)
        return webhook

    @staticmethod
    async def should_post_via_webhook(
        channel: discord.TextChannel, webhook: discord.Webhook, embed: discord.Embed
    ) -> bool:
        history: list[discord.Message] = [
            msg async for msg in channel.history(limit=10)
        ]
        for msg in history:
            if msg.webhook_id == webhook.id and msg.embeds:
                if msg.embeds[0].url == embed.url:
                    return False
        return True


class SessionManager:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def truncate_text(text: str, limit: int = 500) -> str:
    if not text:
        return "*No description.*"
    if len(text) <= limit:
        return text
    truncated: str = text[:limit]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated + "..."

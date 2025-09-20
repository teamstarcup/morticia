import asyncio
from typing import Optional

import discord.ext

from src.awaitable.modal import UserTimeoutException


class AsyncPaginator(discord.ext.pages.Paginator):
    def __init__(self, future: Optional[asyncio.Future] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.future = future or asyncio.get_running_loop().create_future()

    async def send(
        self,
        ctx: discord.ext.commands.Context,
        target: discord.abc.Messageable | None = None,
        target_message: str | None = None,
        reference: None | (
                discord.Message | discord.MessageReference | discord.PartialMessage
        ) = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        mention_author: bool | None = None,
        delete_after: float | None = None,
    ) -> bool:
        await super().send(ctx, target, target_message, reference, allowed_mentions, mention_author, delete_after)
        return await self.future

    async def respond(
        self,
        interaction: discord.Interaction,
        ephemeral: bool = False,
        target: discord.abc.Messageable | None = None,
        **kwargs
    ) -> bool:
        self.update_buttons()

        page: discord.ext.pages.Page | str | discord.Embed | list[discord.Embed] = self.pages[
            self.current_page
        ]
        page_content: discord.ext.pages.Page = self.get_page_content(page)

        if page_content.custom_view:
            self.update_custom_view(page_content.custom_view)

        self.user = interaction.user

        if target:
            if not interaction.response.is_done():
                interaction.response.defer(invisible=True)
            msg = await target.send(
                content=page_content.content,
                embeds=page_content.embeds,
                files=page_content.files,
                view=self,
            )
        elif interaction.response.is_done():
            msg = await interaction.followup.send(
                content=page_content.content,
                embeds=page_content.embeds,
                files=page_content.files,
                view=self,
                ephemeral=ephemeral,
            )
            # convert from WebhookMessage to Message reference to bypass
            # 15min webhook token timeout (non-ephemeral messages only)
            if not ephemeral:
                msg = await msg.channel.fetch_message(msg.id)
        else:
            msg = await interaction.response.send_message(
                content=page_content.content,
                embeds=page_content.embeds,
                files=page_content.files,
                view=self,
                ephemeral=ephemeral,
            )
        if isinstance(msg, (discord.Message, discord.WebhookMessage)):
            self.message = msg
        elif isinstance(msg, discord.Interaction):
            self.message = await msg.original_response()

        return await self.future

    async def cancel(
        self,
        include_custom: bool = False,
        page: None | (str | discord.ext.pages.Page | list[discord.Embed] | discord.Embed) = None,
    ) -> None:
        if not self.future.done():
            self.future.set_result(False)
        await super().cancel(include_custom, page)

    async def disable(
            self,
            include_custom: bool = False,
            page: None | (str | discord.ext.pages.Page | list[discord.Embed] | discord.Embed) = None,
    ) -> None:
        if not self.future.done():
            self.future.set_result(False)
        await super().disable(include_custom, page)

    async def on_error(self, error: Exception, item: discord.ui.Item, interaction: discord.Interaction) -> None:
        self.future.set_exception(error)

    async def on_timeout(self) -> None:
        self.future.set_result(False)

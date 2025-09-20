import asyncio
from asyncio import Future

import discord
from discord import Interaction


class UserTimeoutException(Exception):
    """
    Raised when a user interaction-dependent procedure has timed out due to user inactivity.
    """
    pass


class AsyncModal(discord.ui.Modal):
    future: Future

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.future = asyncio.get_running_loop().create_future()

    async def callback(self, interaction: Interaction):
        self.future.set_result(None)

    async def send(self, interaction: Interaction, *args, **kwargs):
        await interaction.response.send_modal(self, *args, **kwargs)
        return await self.future

    async def on_error(self, error: Exception, interaction: Interaction) -> None:
        """|coro|

        A callback that is called when the modal's callback fails with an error.

        Parameters
        ----------
        error: :class:`Exception`
            The exception that was raised.
        interaction: :class:`~discord.Interaction`
            The interaction that led to the failure.
        """
        self.future.cancel(error)

    async def on_timeout(self) -> None:
        """|coro|

        A callback that is called when a modal's timeout elapses without being explicitly stopped.
        """
        self.future.cancel(UserTimeoutException())


class BeginPortModal(AsyncModal):
    def __init__(self, *args, **kwargs):
        super().__init__(title="Begin Port", *args, **kwargs)

        self.add_item(discord.ui.InputText(label="Pull Request Title"))
        # noinspection PyTypeChecker
        self.add_item(
            discord.ui.InputText(
                label="Pull Request Description",
                style=discord.InputTextStyle.long,
                placeholder="Leave empty to quote from the target pull request",
                required=False
            )
        )

    async def callback(self, interaction: Interaction):
        await interaction.response.defer(invisible=True)
        pr_title = self.children[0].value
        pr_desc = self.children[1].value
        self.future.set_result((pr_title, pr_desc))

    @classmethod
    async def push(cls, interaction: discord.Interaction, *args, **kwargs):
        modal = BeginPortModal(*args, **kwargs)
        return await modal.send(interaction)

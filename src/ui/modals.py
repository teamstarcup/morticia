import discord
from discord import Message

from src.git import PullRequestId
from src.morticia import Morticia


class BeginPortModal(discord.ui.Modal):
    """
    Displayed to the user when they are beginning a port.
    """
    def __init__(self, morticia: Morticia, message: Message, pr_id: PullRequestId, *args, **kwargs) -> None:
        super().__init__(title=f"Begin Port: {pr_id}", *args, **kwargs)

        self.morticia = morticia
        self.message = message
        self.pull_request_id = pr_id

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

    async def callback(self, interaction: discord.Interaction):
        pr_title = self.children[0].value
        pr_desc = self.children[1].value

        try:
            # TODO: find existing threads
            thread = await self.message.create_thread(name=pr_title, auto_archive_duration=1440)
            await thread.add_user(interaction.user)
            await thread.add_user(self.message.author)

            await interaction.response.defer()
            await self.morticia.start_port(self.pull_request_id, pr_title, pr_desc, interaction, thread)

            # await interaction.respond("Okay! Let's get started.", ephemeral=True)

            # send followup dialog here
        except discord.errors.HTTPException as http_exception:
            if "A thread has already been created for this message" not in http_exception.text:
                raise http_exception
            await interaction.respond("A thread has already been created for this message.", ephemeral=True)

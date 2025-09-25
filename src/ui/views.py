from typing import Callable, Coroutine, Any

import discord
from discord.ui import Item

from src.morticia import Morticia
from src.status import StatusMessage
from ..awaitable.modal import BeginPortModal
from ..git import PullRequestId


class MyView(discord.ui.View):
    def __init__(self, port_callback: Callable[[str], Coroutine[Any, Any, None]], morticia: Morticia, pull_request_url: str, *items: Item):
        super().__init__(*items)
        self.port_callback = port_callback
        self.morticia = morticia
        self.pull_request_id = PullRequestId.from_url(pull_request_url)

    @discord.ui.button(label="Port", style=discord.ButtonStyle.primary)
    async def port(self, button: discord.Button, interaction: discord.Interaction):
        button.emoji = "✅"
        button.disabled = True
        try:
            title = await BeginPortModal.push(interaction)
            await self.port_callback(title)
        except discord.errors.HTTPException as http_exception:
            if "A thread has already been created for this message" not in http_exception.text:
                raise http_exception
            await interaction.respond("A thread has already been created for this message.", ephemeral=True)

    @discord.ui.button(label="Find ancestors", style=discord.ButtonStyle.primary)
    async def find_ancestors(self, button: discord.Button, interaction: discord.Interaction):
        status = StatusMessage(interaction)
        await status.write_line(f"Fetching ancestors ...")
        await status.flush()

        ancestors = ""
        for ancestor in self.morticia.get_ancestors(self.pull_request_id):
            ancestors += ancestor + "\n"

            if len(ancestors) > 1500:
                await status.write_line(ancestors)
                await status.flush()
                ancestors = ""

        await status.write_line(ancestors)
        await status.flush()

        await status.write_line("Finished.")
        await status.flush()

    @discord.ui.button(label="Find descendants", style=discord.ButtonStyle.primary)
    async def find_descendants(self, button: discord.Button, interaction: discord.Interaction):
        status = StatusMessage(interaction)
        await status.write_line(f"Fetching descendants ...")
        await status.flush()

        descendants = ""
        for descendant in self.morticia.get_descendants(self.pull_request_id):
            descendants += descendant + "\n"

            if len(descendants) > 1500:
                await status.write_line(descendants)
                await status.flush()
                descendants = ""

        await status.write_line(descendants)
        await status.flush()

        await status.write_line("Finished.")
        await status.flush()

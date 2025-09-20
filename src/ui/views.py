import discord
from discord.ui import Item

from src.morticia import Morticia
from src.status import StatusMessage
from ..awaitable.modal import BeginPortModal
from ..git import PullRequestId


class MyView(discord.ui.View):
    def __init__(self, morticia: Morticia, message: discord.Message, pull_request_url: str, *items: Item):
        super().__init__(*items)
        self.morticia = morticia
        self.original_message = message
        self.pull_request_url = pull_request_url
        self.pull_request_id = PullRequestId.from_url(pull_request_url)

    @discord.ui.button(label="Port", style=discord.ButtonStyle.primary)
    async def port(self, button: discord.Button, interaction: discord.Interaction):
        button.emoji = "✅"
        button.disabled = True
        try:
            pr_title, pr_description = await BeginPortModal.push(interaction)

            thread = await self.original_message.create_thread(name=pr_title, auto_archive_duration=1440)
            await thread.add_user(interaction.user)
            await thread.add_user(self.original_message.author)

            await self.morticia.start_port(self.pull_request_id, pr_title, pr_description, interaction, thread)
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

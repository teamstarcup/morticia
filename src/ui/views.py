import discord
from discord.ui import Item

from src.morticia import Morticia
from src.status import StatusMessage
from ..git import PullRequestId, GitCommandException, MergeConflict


class MyView(discord.ui.View):
    def __init__(self, morticia: Morticia, pull_request_url: str, *items: Item):
        super().__init__(*items)
        self.morticia = morticia
        self.pull_request_url = pull_request_url
        self.pull_request_id = PullRequestId.from_url(pull_request_url)

    @discord.ui.button(label="Port", style=discord.ButtonStyle.primary)
    async def port(self, button: discord.Button, interaction: discord.Interaction):
        try:
            await self.morticia.start_port(self.pull_request_id, interaction)
        except GitCommandException as e:
            await interaction.respond(f"{interaction.user.mention} Encountered a fatal error: \n{e.stdout}\n{e.stderr}")

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

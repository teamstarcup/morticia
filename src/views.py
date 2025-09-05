import discord
from discord.ui import Item

from src.morticia import Morticia
from src.status import StatusMessage
from src.utils import PullRequestId


class MyView(discord.ui.View):
    def __init__(self, morticia: Morticia, pull_request_url: str, *items: Item):
        super().__init__(*items)
        self.morticia = morticia
        self.pull_request_url = pull_request_url
        self.pull_request_id = PullRequestId.from_url(pull_request_url)

    @discord.ui.button(label="Autoport", style=discord.ButtonStyle.primary)
    async def autoport(self, button: discord.Button, interaction: discord.Interaction):
        await interaction.response.send_message("You clicked the button!")

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
        await interaction.response.send_message("You clicked the button!")

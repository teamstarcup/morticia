import asyncio
import os
from asyncio import Future
from typing import Callable, Coroutine, Any, Optional

import discord.ext.pages
from discord import Interaction

from src.awaitable.paginator import AsyncPaginator
from src.git import MergeConflict, ResolutionType
from src.utils import temporary_file


class MergeConflictsContext:
    def __init__(self, conflicts: list[MergeConflict], future: Optional[Future], refresh_callback: Callable[[], Coroutine[Any, Any, None]]):
        self.conflicts = conflicts
        self.future = future
        self.refresh_callback = refresh_callback

    def num_remaining_conflicts(self) -> int:
        """
        Returns the number of conflicts that have yet to be decided.
        :return:
        """
        tally = 0
        for conflict in self.conflicts:
            if conflict.resolution != ResolutionType.UNSELECTED:
                continue
            tally += 1
        return tally

    async def refresh_page(self):
        await self.refresh_callback()

    def update_indicator_button(self, button: discord.ui.Button):
        total_conflicts = len(self.conflicts)
        remaining_conflicts = self.num_remaining_conflicts()
        button.label = f"Progress: {total_conflicts - remaining_conflicts}/{total_conflicts}"

    def update_continue_button(self, button: discord.ui.Button):
        button.disabled = self.num_remaining_conflicts() > 0

    def continue_merge(self):
        # mark Task resolved to proceed with merge
        # probably also disable the paginator?
        self.future.set_result(True)

    def cancel_merge(self):
        # mark Task as rejected or something
        self.future.set_result(False)
        pass


class ResolveConflictModal(discord.ui.Modal):
    """
    For manual merge conflict resolution.
    """

    def __init__(self, conflict: MergeConflict, *args, **kwargs) -> None:
        super().__init__(title="Resolve Conflict", *args, **kwargs)

        self.conflict = conflict

        # noinspection PyTypeChecker
        self.add_item(
            discord.ui.InputText(
                label="Contents",
                style=discord.InputTextStyle.long,
                value=conflict.content,
                required=False
            )
        )

    async def callback(self, interaction: discord.Interaction):
        new_contents = self.children[0].value
        self.conflict.proposed_content = new_contents
        await interaction.response.defer()


class MergeConflictsPaginator(discord.ext.pages.Paginator):
    pass


class MergeConflictCountIndicator(discord.ui.Button):
    """
    Displays the number of remaining unconfigured conflicts
    """
    def __init__(self, ctx: MergeConflictsContext, **kwargs):
        super().__init__(label="0/0", row=2, disabled=True, **kwargs)
        self.ctx = ctx
        self.update()

    def update(self):
        self.ctx.update_continue_button(self)


class MergeConflictContinueButton(discord.ui.Button):
    """
    Proceeds with resolving the conflicts and continuing the merge process
    """
    def __init__(self, ctx: MergeConflictsContext, **kwargs):
        super().__init__(label="Continue", row=2, disabled=True, style=discord.ButtonStyle.success, **kwargs)
        self.ctx = ctx

    def update(self):
        self.ctx.update_continue_button(self)

    async def callback(self, interaction: Interaction):
        await interaction.response.defer()
        self.ctx.continue_merge()


class MergeConflictView(discord.ui.View):
    """
    Displayed for each page in the MergeConflictsPaginator, for operating controls for resolving the conflict.
    """
    def __init__(self, conflict: MergeConflict, ctx: MergeConflictsContext):
        super().__init__()
        self.conflict = conflict
        self.ctx = ctx

        self.indicator_button = MergeConflictCountIndicator(ctx)
        self.add_item(self.indicator_button)
        self.continue_button = MergeConflictContinueButton(ctx)
        self.add_item(self.continue_button)

        self.checked_button: Optional[discord.Button] = None
        self.refreshing = False

    async def update_buttons(self):
        for child in self.children:
            if child == self.checked_button:
                child.emoji = "✅"
            else:
                child.emoji = None

        self.ctx.update_indicator_button(self.indicator_button)
        self.ctx.update_continue_button(self.continue_button)

        self.refreshing = True
        await self.ctx.refresh_page()
        self.refreshing = False

    @discord.ui.button(label="Edit")
    async def edit(self, button: discord.Button, interaction: discord.Interaction):
        self.checked_button = button
        self.conflict.take_manual()
        await interaction.response.send_modal(ResolveConflictModal(self.conflict))
        await self.update_buttons()

    @discord.ui.button(label="Ours")
    async def ours(self, button: discord.Button, interaction: discord.Interaction):
        self.checked_button = button
        self.conflict.take_ours()
        await interaction.response.defer()
        await self.update_buttons()

    @discord.ui.button(label="Theirs")
    async def theirs(self, button: discord.Button, interaction: discord.Interaction):
        self.checked_button = button
        self.conflict.take_theirs()
        await interaction.response.defer()
        await self.update_buttons()

    @discord.ui.button(label="Fix it later")
    async def fix_later(self, button: discord.Button, interaction: discord.Interaction):
        self.checked_button = button
        self.conflict.as_is()
        await interaction.response.defer()
        await self.update_buttons()

    @discord.ui.button(label="Cancel", row=2)
    async def cancel(self, _: discord.Button, interaction: discord.Interaction):
        await interaction.response.defer()
        self.ctx.cancel_merge()


MAX_EMBED_LENGTH = 4096
class MergeConflictPage(discord.ext.pages.Page):
    def __init__(self, conflict: MergeConflict, ctx: MergeConflictsContext, **kwargs):
        self.conflict = conflict
        self.ignore_callback = False

        diff = conflict.diff or f"Binary file"

        # truncate lengthy diffs and upload them as attachments
        files = []
        if len(diff) > MAX_EMBED_LENGTH:
            diff = f"{diff[:4000]}\n\x1B[0m...\nTruncated diff"
            file_name = f"{os.path.basename(conflict.path)}.diff.txt"
            diff_output_file = temporary_file(conflict.diff, filename=file_name)
            files.append(diff_output_file)

        content_file = temporary_file(conflict.content, f"{os.path.basename(conflict.path)}")
        files.append(content_file)

        desc = f"```ansi\n{diff}\n```"
        self.view = MergeConflictView(conflict, ctx)
        super().__init__(embeds=[discord.Embed(title=f"{conflict.path}", description=desc)], custom_view=self.view, files=files, **kwargs)

    async def callback(self, interaction: discord.Interaction | None = None):
        # Called when this page is displayed
        if not self.view.refreshing:
            await self.view.update_buttons()


# noinspection PyRedeclaration
class MergeConflictsPaginator(AsyncPaginator):
    def __init__(self, conflicts: list[MergeConflict], **kwargs):
        future = asyncio.get_running_loop().create_future()
        conflicts_ctx = MergeConflictsContext(conflicts, future, self._refresh)
        pages = [MergeConflictPage(conflict, conflicts_ctx) for conflict in conflicts]
        super().__init__(pages=pages, default_button_row=1, timeout=900 - 1, trigger_on_display=True, **kwargs)

    async def _refresh(self):
        await self.goto_page(self.current_page)

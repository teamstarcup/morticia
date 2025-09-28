import logging
import os
import random
import re
import sys
import time
import traceback
from datetime import datetime
from typing import Optional, Any

import discord
import discord.ext
from sqlalchemy.orm import Session

from src.awaitable.modal import BeginPortModal
from src.git import RepoId, PullRequestId, LocalRepo
from src.model import KnownPullRequest
from src.morticia import Morticia, Project
from src.ui.views import MyView
from src.utils import parse_pull_request_urls, pretty_duration, parse_repo_urls, temporary_file, send_embedded_output

GUILD_IDS = os.environ.get("DISCORD_GUILD_IDS").split(",")
USER_ROLE_IDS = os.environ.get("USER_ROLE_IDS").split(",")

log = logging.getLogger(__name__)


class MorticiaBot(discord.Bot):
    session: Session

    def __init__(self, morticia: Morticia, *args, **options):
        super().__init__(*args, **options)

        self.morticia = morticia

    async def on_ready(self):
        log.info(f"We have logged in as {self.user}")

    async def handle_exception(self, exception: Exception, interaction: discord.Interaction):
        self.session.rollback()

        trace = "".join(traceback.format_exception(exception))
        trace = trace.replace(self.morticia.auth.token, "<REDACTED>")
        traceback.print_exception(exception)

        message = "Unhandled exception:"
        if interaction.response.is_done():
            message = f"{interaction.user.mention} {message}"
        await interaction.respond(message, file=temporary_file(trace, filename="trace.txt"))

        jump_url = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel.id}/{interaction.id}"
        log.error(f"Printed exception to Discord: {jump_url}")

    async def on_application_command_error(self, ctx: discord.ApplicationContext,
                                           error: discord.ext.commands.errors.CommandError):
        command = ctx.command
        if command and command.has_error_handler():
            return

        cog = ctx.cog
        if cog and cog.has_error_handler():
            return

        if isinstance(error, discord.errors.ApplicationCommandInvokeError):
            error = error.original

        if isinstance(error, discord.ext.commands.CommandOnCooldown):
            await ctx.respond(f"This command is on cooldown, you can use it in {round(error.retry_after, 2)} seconds",
                              ephemeral=True)
        elif isinstance(error, discord.ext.commands.errors.MissingAnyRole):
            await ctx.respond(f"You are missing role permissions required to run this command.", ephemeral=True)
        elif isinstance(error, discord.errors.HTTPException) and "A thread has already been created for this message" in error.text:
            await ctx.respond(f"A thread has already been created for this message.", ephemeral=True)
        else:
            await self.handle_exception(error, ctx.interaction)

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any):
        interaction, _ = args
        exception, _, _ = sys.exc_info()
        await self.handle_exception(exception, interaction)

    async def start_port(self, interaction: discord.Interaction, message: discord.Message, pr_id: PullRequestId, title: str):
        if isinstance(interaction.channel, discord.Thread):
            thread = interaction.channel
            if thread.owner != self.user:
                await interaction.respond("Sorry, you can't use this command inside a non-port thread!")
                return
        else:
            thread = await message.create_thread(name=title, auto_archive_duration=1440)
            await thread.add_user(interaction.user)
            await thread.add_user(message.author)

        work_repo = await LocalRepo.open(self.morticia.work_repo_id)

        project = await Project.create(thread, work_repo, self.morticia.github, self.session)
        with project:
            await project.prepare_repo(self.morticia.auth.token)
            success = await project.add_pull_request_interactive(pr_id, interaction)
            if not success:
                # process was timed out or cancelled by user
                return
            # await thread.send("I would have submitted a pull request, but this was a dry run.")
            new_pull_request = await project.create_pull_request(title, pr_id)
            await thread.send(f"Complete: {new_pull_request.html_url}")


def create_bot(*args, **kwargs):
    bot = MorticiaBot(*args, **kwargs)


    @bot.slash_command(
        description="You reach out to pet Morticia...",
        guild_ids=GUILD_IDS,
    )
    async def pet(ctx: discord.ApplicationContext):
        success = random.random() >= 0.7
        if not success:
            await ctx.respond(f"-# You reach out to pet Morticia, but she is busy raccooning around.")
        else:
            await ctx.respond(f"-# You pet Morticia on her trash eating little head. 💕 🦝")


    @bot.slash_command(
        description="Search for commits that have changed a file",
        guild_ids=GUILD_IDS,
    )
    async def files(ctx: discord.ApplicationContext, file_path: str, repo_id: str):
        await ctx.defer()

        repo_id: RepoId = RepoId.from_string(repo_id)
        revision = f"{repo_id.slug()}/HEAD"

        work_repo = await LocalRepo.open(bot.morticia.work_repo_id)

        # recursive search to find the most recent path of the given file
        # git's `--follow` will not suffice: it can only follow renames going backward through history
        recursion = True
        while recursion:
            recursion = False

            file_change_commits = await work_repo.list_commits_changing_file(revision, file_path=file_path)

            if len(file_change_commits) <= 0:
                break

            renamed_files = await work_repo.list_renamed_files_in_commit(file_change_commits[0])
            for renamed_file in renamed_files:
                if not renamed_file.before == file_path:
                    continue

                file_path = renamed_file.after
                recursion = True
                break

        file_change_commits = await work_repo.list_commits_changing_file(revision, file_path=file_path, format_opt="--oneline", opts="--follow")
        await send_embedded_output(ctx, "\n".join(file_change_commits))


    @bot.message_command(
        description="Begin porting for this PR. You will be prompted for more details.",
        guild_ids=GUILD_IDS
    )
    @discord.ext.commands.has_any_role(*USER_ROLE_IDS)
    async def port(ctx: discord.ApplicationContext, message: discord.Message):
        pull_request_ids = parse_pull_request_urls(message.content)
        if len(pull_request_ids) <= 0:
            await ctx.respond("Hey, I didn't find any pull request links there.")
            return

        pull_request_id = pull_request_ids.pop()

        title = await BeginPortModal.push(ctx.interaction)
        await bot.start_port(ctx.interaction, message, pull_request_id, title)

    @bot.message_command(
        description="Open a dialogue of actions for a given PR.",
        guild_ids=GUILD_IDS,
    )
    @discord.ext.commands.has_any_role(*USER_ROLE_IDS)
    async def explore(ctx: discord.ApplicationContext, message: discord.Message):
        pull_request_ids = parse_pull_request_urls(message.content)
        if len(pull_request_ids) <= 0:
            await ctx.respond("Hey, I didn't find any pull request links there.")
            return

        pull_request_id = pull_request_ids.pop()
        pull_request = bot.morticia.get_pull_request(pull_request_id)

        body = pull_request.body or ""
        body_summary = re.sub(r"<!--.*?-->", "", body)[:300]
        if len(body) > 300:
            body_summary += " ..."
        body_summary += os.linesep
        body_summary += f"```ansi\n[2;36m+{pull_request.additions}[0m [2;31m-{pull_request.deletions}[0m\n```"

        color = discord.Colour.green()
        if pull_request.merged:
            color = discord.Colour.purple()
        elif pull_request.state == "closed":
            color = discord.Colour.red

        embed = discord.Embed(
            title=pull_request.title,
            description=body_summary,
            url=pull_request_id.url,
            color=color,
        )
        embed.add_field(
            name="State",
            value=pull_request.merged and "Merged" or pull_request.state,
        )
        embed.add_field(
            name="Created: ",
            value=f"<t:{int(pull_request.created_at.timestamp())}:f>",
        )
        if pull_request.state == "closed":
            embed.add_field(
                name="Closed: ",
                value=f"<t:{int(pull_request.closed_at.timestamp())}:f>",
            )

        embed.set_author(
            name=pull_request.user.login,
            icon_url=pull_request.user.avatar_url,
            url=f"https://github.com/{pull_request.user.login}",
        )

        def callback(title: str):
            return bot.start_port(ctx.interaction, message, pull_request_id, title)
        await ctx.respond("", embed=embed, view=MyView(callback, bot.morticia, pull_request_id.url))


    @bot.slash_command(
        description="Indexes all pull requests in the given GitHub repository.",
        guild_ids=GUILD_IDS,
    )
    @discord.ext.commands.has_any_role(*USER_ROLE_IDS)
    async def index(ctx: discord.ApplicationContext, repo_url: str):
        repo_ids = parse_repo_urls(repo_url)
        if len(repo_ids) <= 0:
            await ctx.respond("Hey, I didn't find any GitHub repository links there.")
            return

        repo_id = repo_ids.pop()
        pull_request_count = bot.morticia.get_github_repo(repo_id).get_pulls("all").totalCount
        estimated_seconds = pull_request_count * 4
        estimate = pretty_duration(estimated_seconds)
        await ctx.respond(f"Okay, I'll go index {repo_id}. This is probably going to take a lot longer than 15 minutes,"
                          f" so I'll ping you when I'm done!\n\nEstimated time: {estimate}")

        time_start = time.time()
        await bot.morticia.index_repo(repo_id)
        time_stop = time.time()
        duration = int(time_stop - time_start)
        display_duration = pretty_duration(duration)

        await ctx.send(f"{ctx.user.mention} Done indexing {repo_id} in {display_duration}!")


    @bot.message_command(
        description="Test command",
        guild_ids=GUILD_IDS,
    )
    @discord.ext.commands.has_any_role(*USER_ROLE_IDS)
    async def modal(ctx: discord.ApplicationContext, message: discord.Message):
        pull_requests = parse_pull_request_urls(message.content)
        if len(pull_requests) <= 0:
            await ctx.respond("Hey, I didn't find any pull request links there.")
            return

        pr_title, pr_description = await BeginPortModal.push(ctx.interaction)
        await ctx.respond(f"Title: {pr_title}\nDesc: {pr_description}", ephemeral=True)


    @bot.slash_command(
        description="Search for pull requests that change a file.",
        guild_ids=GUILD_IDS,
    )
    @discord.ext.commands.has_any_role(*USER_ROLE_IDS)
    async def search(ctx: discord.ApplicationContext, path: str, repo_id: Optional[str]):
        repo_id = repo_id is not None and RepoId.from_string(repo_id) or None
        known_pull_requests = bot.morticia.search_for_file_changes(path, repo_id)

        # known_pull_requests = morticia.get_upstream_merge_prs(repo_id)

        def sort_by_oldest(element: KnownPullRequest):
            return element.merged_at or datetime.fromisocalendar(1970, 1, 1)

        known_pull_requests.sort(key=sort_by_oldest)

        text = ""
        for pull_request in known_pull_requests:
            # text += f"- [{pull_request.pull_request_id} - {pull_request.title}]({pull_request.html_url})" + "\n"
            text += f"- {pull_request.pull_request_id} - {pull_request.title}" + "\n"

        if len(text) > 6000:
            await ctx.send("Truncating message to 6000 characters")
            text = text[:6000]

        embeds = []
        PAGE_SIZE = 4096
        total_pages = max(int(len(text) / PAGE_SIZE), 1)
        for i in range(min(total_pages, 10)):
            page = text[PAGE_SIZE * i:PAGE_SIZE * (i + 1)]
            embeds.append(discord.Embed(
                title=f"[{i + 1}/{total_pages}] Changes to {path}",
                description=page,
            ))

        await ctx.respond(embeds=embeds)

    @bot.slash_command(
        description="Perform a preference poll in this channel.",
        guild_ids=GUILD_IDS,
    )
    async def poll(ctx: discord.ApplicationContext, question: Optional[str] = "What is your impression?", hours: Optional[int] = 24):
        poll = discord.Poll(question=question, duration=hours, allow_multiselect=True)
        poll.add_answer("Hate it", emoji="❌")
        poll.add_answer("Dislike it", emoji="😕")
        poll.add_answer("Neutral", emoji="🆗")
        poll.add_answer("Like it", emoji="🙂")
        poll.add_answer("Love it", emoji="❤️")
        poll.add_answer("I'd like to see significant or structural changes to it", emoji="🤔")

        await ctx.respond(poll=poll)


    return bot

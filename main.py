import datetime
import logging
import os
import random
import re
import sys
import time
import traceback
from typing import Optional

import discord
import dotenv
from discord.ext import pages
from discord.ext.commands import cooldown, MissingAnyRole
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.git import PullRequestId, RepoId
from src.model import KnownPullRequest
from src.morticia import Morticia
from src.utils import get_pr_links_from_text, get_repo_links_from_text, pretty_duration
from src.ui.views import MyView
from src.ui.modals import BeginPortModal

dotenv.load_dotenv(".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.DEBUG,
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

token = os.environ.get("GITHUB_TOKEN")
username = os.environ.get("GITHUB_BOT_USERNAME")
email = os.environ.get("GITHUB_BOT_EMAIL")

GUILD_IDS = os.environ.get("DISCORD_GUILD_IDS").split(",")
USER_ROLE_IDS = os.environ.get("USER_ROLE_IDS").split(",")

db_host = os.environ.get("POSTGRES_HOST")
db_port = os.environ.get("POSTGRES_PORT")
db_user = os.environ.get("POSTGRES_USER")
db_pass = os.environ.get("POSTGRES_PASSWORD")
db_name = os.environ.get("POSTGRES_DB")
engine = create_engine(f'postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}')


STACK_TRACE_FILE_PATH = ".trace.ignore"


class MorticiaBot(discord.Bot):
    session: Session

    def __init__(self, *args, **options):
        super().__init__(*args, **options)

    async def on_ready(self):
        log.info(f"We have logged in as {self.user}")

    async def handle_exception(self, exception: Exception, interaction: discord.Interaction):
        self.session.rollback()

        trace = "".join(traceback.format_exception(exception))
        trace = trace.replace(token, "<REDACTED>")
        traceback.print_exception(exception)

        with open(STACK_TRACE_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(trace)

        message = "Unhandled exception:"
        if interaction.response.is_done():
            message = f"{interaction.user.mention} {message}"
        await interaction.respond(message, file=discord.File(fp=STACK_TRACE_FILE_PATH, filename="trace.txt"))

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

        if isinstance(error, discord.ext.commands.CommandOnCooldown):
            await ctx.respond(f"This command is on cooldown, you can use it in {round(error.retry_after, 2)} seconds",
                              ephemeral=True)
        elif isinstance(error, MissingAnyRole):
            await ctx.respond(f"You are missing role permissions required to run this command.", ephemeral=True)
        else:
            await self.handle_exception(error, ctx.interaction)


intents = discord.Intents.all()
bot = MorticiaBot()


# noinspection PyTypeChecker
@bot.slash_command(
    name="pet",
    description="You reach out to pet Morticia...",
    guild_ids=GUILD_IDS,
)
@cooldown(1, 10, discord.ext.commands.BucketType.user)
async def pet(ctx: discord.ApplicationContext):
    success = random.random() >= 0.7
    if not success:
        await ctx.respond(f"-# You reach out to pet Morticia, but she is busy raccooning around.")
    else:
        await ctx.respond(f"-# You pet Morticia on her trash eating little head. 💕 🦝")


@bot.message_command(
    name="explore",
    description="Open a dialogue of actions for a given PR.",
    guild_ids=GUILD_IDS,
)
@discord.ext.commands.has_any_role(*USER_ROLE_IDS)
async def explore(ctx: discord.ApplicationContext, message: discord.Message):
    matches: list[str] = get_pr_links_from_text(message.content)
    if len(matches) <= 0:
        await ctx.respond("Hey, I didn't find any pull request links there.")
        return

    pull_request_url = matches[0]

    pull_request_id = PullRequestId.from_url(pull_request_url)
    pull_request = morticia.get_pull_request(pull_request_id)

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
        url=pull_request_url,
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
    await ctx.respond("", embed=embed, view=MyView(morticia, pull_request_url))


@bot.slash_command(
    name="index",
    description="Indexes all pull requests in the given GitHub repository.",
    guild_ids=GUILD_IDS,
)
@discord.ext.commands.has_any_role(*USER_ROLE_IDS)
async def index(ctx: discord.ApplicationContext, repo_url: str):
    matches = get_repo_links_from_text(repo_url)
    if len(matches) <= 0:
        await ctx.respond("Hey, I didn't find any GitHub repository links there.")
        return

    repo_id = RepoId.from_url(matches[0])
    pull_request_count = morticia.get_github_repo(repo_id).get_pulls("all").totalCount
    estimated_seconds = pull_request_count * 4
    estimate = pretty_duration(estimated_seconds)
    await ctx.respond(f"Okay, I'll go index {repo_id}. This is probably going to take a lot longer than 15 minutes,"
                      f" so I'll ping you when I'm done!\n\nEstimated time: {estimate}")

    time_start = time.time()
    await morticia.index_repo(repo_id)
    time_stop = time.time()
    duration = int(time_stop - time_start)
    display_duration = pretty_duration(duration)

    await ctx.send(f"{ctx.user.mention} Done indexing {repo_id} in {display_duration}!")


@bot.message_command(
    name="port",
    description="Begin porting for this PR. You will be prompted for more details.",
    guild_ids=GUILD_IDS,
)
@discord.ext.commands.has_any_role(*USER_ROLE_IDS)
async def port(ctx: discord.ApplicationContext, message: discord.Message):
    matches: list[str] = get_pr_links_from_text(message.content)
    if len(matches) <= 0:
        await ctx.respond("Hey, I didn't find any pull request links there.")
        return

    pull_request_url = matches[0]
    pull_request_id = PullRequestId.from_url(pull_request_url)
    modal = BeginPortModal(morticia, message, pull_request_id)
    await ctx.send_modal(modal)


@bot.slash_command(
    name="search",
    description="Search for pull requests that change a file.",
    guild_ids=GUILD_IDS,
)
@discord.ext.commands.has_any_role(*USER_ROLE_IDS)
async def search(ctx: discord.ApplicationContext, path: str, repo_id: Optional[str]):
    repo_id = repo_id is not None and RepoId.from_string(repo_id) or None
    known_pull_requests = morticia.search_for_file_changes(path, repo_id)
    # known_pull_requests = morticia.get_upstream_merge_prs(repo_id)

    def sort_by_oldest(element: KnownPullRequest):
        return element.merged_at or datetime.datetime.fromisocalendar(1970, 1, 1)
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
        page = text[PAGE_SIZE*i:PAGE_SIZE*(i+1)]
        embeds.append(discord.Embed(
            title=f"[{i + 1}/{total_pages}] Changes to {path}",
            description=page,
        ))

    await ctx.respond(embeds=embeds)

with Session(engine) as session:
    morticia = Morticia(token, session)
    bot.session = session
    bot.run(os.environ.get("DISCORD_TOKEN"))

morticia.close()

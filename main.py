import logging
import os
import random
import re
import sys
import time

import discord
import dotenv
from discord.ext import pages
from discord.ext.commands import cooldown
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.git import PullRequestId, RepoId
from src.morticia import Morticia
from src.utils import get_pr_links_from_text, get_repo_links_from_text, pretty_duration, complains, complain
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

db_host = os.environ.get("POSTGRES_HOST")
db_port = os.environ.get("POSTGRES_PORT")
db_user = os.environ.get("POSTGRES_USER")
db_pass = os.environ.get("POSTGRES_PASSWORD")
db_name = os.environ.get("POSTGRES_DB")
engine = create_engine(f'postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}')


class MorticiaBot(discord.Bot):
    session: Session

    def __init__(self, *args, **options):
        super().__init__(*args, **options)


intents = discord.Intents.all()
bot = MorticiaBot()


@bot.event
async def on_ready():
    log.info(f"We have logged in as {bot.user}")


# noinspection PyTypeChecker
@bot.slash_command(
    name="pet",
    description="You reach out to pet Morticia...",
    #guild_ids=[os.environ.get("DISCORD_GUILD_ID")],
)
@cooldown(1, 10, discord.ext.commands.BucketType.user)
async def pet(ctx: discord.ApplicationContext):
    success = random.random() >= 0.7
    if not success:
        await ctx.respond(f"-# You reach out to pet Morticia, but she is busy raccooning around.")
    else:
        await ctx.respond(f"-# You pet Morticia on her trash eating little head. 💕 🦝")


@pet.error
@complains
async def on_command_error(ctx, error):
    if isinstance(error, discord.ext.commands.CommandOnCooldown):
        await ctx.respond(f"This command is on cooldown, you can use it in {round(error.retry_after, 2)} seconds", ephemeral=True)
    else:
        raise error


@bot.message_command(
    name="explore",
    description="Open a dialogue of actions for a given PR.",
    default_member_permissions=discord.Permissions(
        discord.Permissions.ban_members.flag
    ),
    guild_ids=[os.environ.get("DISCORD_GUILD_ID")],
)
@complains
async def explore(ctx: discord.ApplicationContext, message: discord.Message):
    matches: list[str] = get_pr_links_from_text(message.content)
    if matches == 0:
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


@explore.error
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
    raise error  # Here we raise other errors to ensure they aren't ignored


@bot.slash_command(
    name="index",
    description="Indexes all pull requests in the given GitHub repository.",
    default_member_permissions=discord.Permissions(
        discord.Permissions.ban_members.flag
    ),
    guild_ids=[os.environ.get("DISCORD_GUILD_ID")],
    options = [],
)
async def index(ctx: discord.ApplicationContext, repo_url: str):
    # noinspection PyBroadException
    try:
        matches = get_repo_links_from_text(repo_url)
        if matches == 0:
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
    except Exception:
        await complain(ctx)


@bot.message_command(
    name="port",
    description="Begin porting for this PR. You will be prompted for more details.",
    default_member_permissions=discord.Permissions(
        discord.Permissions.ban_members.flag
    ),
    guild_ids=[os.environ.get("DISCORD_GUILD_ID")],
)
@complains
async def port(ctx: discord.ApplicationContext, message: discord.Message):
    modal = BeginPortModal(message, title="Begin Port")
    await ctx.send_modal(modal)


with Session(engine) as session:
    morticia = Morticia(token, session)
    bot.session = session
    bot.run(os.environ.get("DISCORD_TOKEN"))

morticia.close()

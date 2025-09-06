import encodings
import logging
import os
import random
import re
import sys
import time
import traceback

import discord
import dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.morticia import Morticia
from src.utils import get_pr_links_from_text, get_repo_links_from_text, pretty_duration, RepoId, PullRequestId
from src.views import MyView

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

intents = discord.Intents.all()
bot = discord.Bot()


async def complain(ctx: discord.ApplicationContext):
    traceback.print_exc()
    session.rollback()
    message = f"{ctx.user.mention} Unhandled exception:"
    with open("trace.txt", "w", encoding=encodings.utf_8) as f:
        f.write(traceback.format_exc())
    await ctx.send(message, file=discord.File(fp="trace.txt"))


def complains(func):
    """
    Decorator function for bot commands to automatically respond with any unhandled exceptions.
    :param func:
    :return:
    """
    async def wrapper(*args, **kwds):
        # noinspection PyBroadException
        try:
            await func(*args, **kwds)
        except Exception:
            ctx: discord.ApplicationContext = args[0]
            await complain(ctx)
        return None

    return wrapper


@bot.event
async def on_ready():
    log.info(f"We have logged in as {bot.user}")


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
        estimated_seconds = pull_request_count * 2.5
        estimate = pretty_duration(estimated_seconds)
        await ctx.respond(f"Okay, I'll go index {repo_id}. This is probably going to take a lot longer than 15 minutes,"
                          f" so I'll ping you when I'm done!\n\nEstimated time: {estimate}")

        time_start = time.time()
        morticia.index_repo(repo_id)
        time_stop = time.time()
        duration = int(time_stop - time_start)
        display_duration = pretty_duration(duration)

        await ctx.send(f"{ctx.user.mention} Done indexing {repo_id} in {display_duration}!")
    except Exception:
        await complain(ctx)


with Session(engine) as session:
    morticia = Morticia(token, session)
    bot.run(os.environ.get("DISCORD_TOKEN"))

morticia.close()

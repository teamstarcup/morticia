import logging
import os
import re
import sys
import traceback

import discord
import dotenv
from discord import DiscordException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.morticia import Morticia
from src.utils import get_pr_links_from_text
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
morticia = Morticia(token)

db_host = os.environ.get("POSTGRES_HOST")
db_port = os.environ.get("POSTGRES_PORT")
db_user = os.environ.get("POSTGRES_USER")
db_pass = os.environ.get("POSTGRES_PASSWORD")
db_name = os.environ.get("POSTGRES_DB")
engine = create_engine(f'postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}')

intents = discord.Intents.all()
bot = discord.Bot()


def complains(func):
    """
    Decorator function for bot commands to automatically respond with any unhandled exceptions.
    :param func:
    :return:
    """
    async def wrapper(*args, **kwds):
        try:
            await func(*args, **kwds)
        except Exception:
            ctx: discord.ApplicationContext = args[0]
            message = f"{ctx.user.mention} Unhandled exception:\n```\n{traceback.format_exc()}```"
            try:
                await ctx.respond(message)
            except DiscordException:
                await ctx.channel.send(message)
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

    repo_id = Morticia.repo_id_from_url(pull_request_url)

    pull_request = morticia.get_pull_request(pull_request_url)

    body_summary = re.sub(r"<!--.*?-->", "", pull_request.body)[:300]
    if len(pull_request.body) > 300:
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


with Session(engine) as session:
    bot.run(os.environ.get("DISCORD_TOKEN"))

morticia.close()

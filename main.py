import logging
import os
import sys
import time
import traceback

import discord
import dotenv
from discord import DiscordException

from src.morticia import Morticia
from src.utils import get_pr_links_from_text, pretty_duration

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

    (organization_name, repo_name) = Morticia.repo_id_from_url(pull_request_url)
    repo_id = f"{organization_name}/{repo_name}"
    await ctx.respond(f"```\nFetching the latest changes to {repo_id} ...```")

    start_time = time.time()
    morticia.pull_repo(pull_request_url)
    end_time = time.time()
    pretty_time = pretty_duration(int(end_time - start_time))

    await ctx.respond(f"Done in {pretty_time}!")


bot.run(os.environ.get("DISCORD_TOKEN"))
morticia.close()

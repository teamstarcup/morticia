import logging
import os
import re
import sys
import time

import discord
import dotenv

from starcatcher import Starcatcher, MergeConflictException

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
starcatcher = Starcatcher(token, "teamstarcup/starcup", username, email)

intents = discord.Intents.default()
bot = discord.Bot()


PULL_REQUEST_LINK_PATTERN = re.compile(
    r"(https://github.com/[\w\-_]+/[\w\-_]+/pull/\d+)"
)


def pretty_duration(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    minutes = int(minutes)
    seconds = int(seconds)
    pretty_time = ""
    if minutes > 0:
        minutes_text = minutes > 1 and "minutes" or "minute"
        pretty_time += f"{minutes:.0f} {minutes_text}"
    if seconds > 0:
        if minutes > 0:
            pretty_time += " and "
        seconds_text = int(seconds) > 1 and "seconds" or "second"
        pretty_time += f"{seconds:.0f} {seconds_text}"
    return pretty_time


@bot.event
async def on_ready():
    log.info(f"We have logged in as {bot.user}")


@bot.message_command(
    description="Begin the process of automatically porting the given pull request.",
    default_member_permissions=discord.Permissions(
        discord.Permissions.ban_members.flag
    ),
    guild_ids=[os.environ.get("DISCORD_GUILD_ID")],
)
async def port(ctx: discord.ApplicationContext, message: discord.Message):
    matches: list[str] = re.findall(PULL_REQUEST_LINK_PATTERN, message.content)
    if matches == 0:
        await ctx.respond("Hey, I didn't find any pull request links there.")
        return

    pull_request_url = matches[0]

    try:
        await ctx.defer()
        start_time = time.time()
        pull_request = starcatcher.port(pull_request_url)
        end_time = time.time()
    except MergeConflictException as e:
        await ctx.respond(f"Unable to port due to merge conflicts!")
        return

    pretty_time = pretty_duration(int(end_time - start_time))
    await ctx.respond(f"Done in {pretty_time}! 🥰\n\n{pull_request.html_url}")
    # await ctx.respond(f"Done in {pretty_time}! 🥰\n\nabc")


@bot.message_command(
    description="Fetch details about a given pull request",
    default_member_permissions=discord.Permissions(
        discord.Permissions.ban_members.flag
    ),
    guild_ids=[os.environ.get("DISCORD_GUILD_ID")],
)
async def check(ctx: discord.ApplicationContext, message: discord.Message):
    matches: list[str] = re.findall(PULL_REQUEST_LINK_PATTERN, message.content)
    if matches == 0:
        await ctx.respond("Hey, I didn't find any pull request links there.")
        return

    pull_request_url = matches[0]
    pull_request = starcatcher.get_pull_request(pull_request_url)

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
    await ctx.respond("", embed=embed)


@bot.slash_command()
async def hello(ctx: discord.ApplicationContext):
    await ctx.respond("Hello!")


bot.run(os.environ.get("DISCORD_TOKEN"))


# starcatcher.port("https://github.com/impstation/imp-station-14/pull/2903") # failed on merge conflicts
# starcatcher.port("https://github.com/impstation/imp-station-14/pull/2800")  # fruit, meat bowls

# starcatcher.port("https://github.com/DeltaV-Station/Delta-v/pull/859")  # prescription huds

starcatcher.close()

import re
import traceback

import discord

REPO_LINK_PATTERN = re.compile(
    r"(https://github.com/[\w\-_]+/[\w\-_]+/?)"
)

PULL_REQUEST_LINK_PATTERN = re.compile(
    r"(https://github.com/[\w\-_]+/[\w\-_]+/pull/\d+)"
)


def get_pr_links_from_text(text: str) -> list[str]:
    return re.findall(PULL_REQUEST_LINK_PATTERN, text)


def get_repo_links_from_text(text: str) -> list[str]:
    return re.findall(REPO_LINK_PATTERN, text)


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


async def complain(ctx: discord.ApplicationContext):
    traceback.print_exc()
    # session.rollback()
    message = f"{ctx.user.mention} Unhandled exception:"
    with open("trace.txt", "w", encoding="utf-8") as f:
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

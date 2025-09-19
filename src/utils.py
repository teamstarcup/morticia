import re
import traceback

import discord

from src.git import RepoId

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


IMPLICIT_ISSUE_PATTERN = re.compile(r"(?:^|[^\w`])(#\d+)(?:[^\w`]|$)")
def qualify_implicit_issues(message: str, repo_id: RepoId) -> str:
    """
    Finds implicit issue links (e.g. "#1234") and expands them to explicit issue links (e.g. "org/repo#1234")
    :param message:
    :param repo_id:
    :return:
    """
    return IMPLICIT_ISSUE_PATTERN.sub(rf" `{repo_id}\1`", message)


EXPLICIT_ISSUE_PATTERN = re.compile(r"\s([\w\.-]+/[\w\.-]+#\d+)")
ABSOLUTE_REFERENCE_PATTERN = re.compile(r"((?:https?://)?[^\.]?github.com/)[\w\.-]+/[\w\.-]+/(pull|issue|commit)/[a-f\d]+")
DIRECT_BASE_URL = "https://github.com/"
INDIRECT_BASE_URL = "https://redirect.github.com/"
USERNAME_MENTION_PATTERN = re.compile(r"(@[A-Za-z0-9_\.-]+)")
def obscure_references(message: str) -> str:
    """
    Replaces off-repo links with obscured versions which do not generate backlinks.
    :param message:
    :return:
    """

    # explicit issue refs: impstation/imp-station-14#123
    # for match in EXPLICIT_ISSUE_PATTERN.findall(message):
    #     repo_id, issue_number = match.split("#")
    #     message = message.replace(match, f"[{match}]({INDIRECT_BASE_URL}{repo_id}/pull/{issue_number})")
    message = EXPLICIT_ISSUE_PATTERN.sub(rf"`\1`", message)

    # absolute URLs: https://github.com/impstation/imp-station-14/pull/123
    message = ABSOLUTE_REFERENCE_PATTERN.sub(rf"{INDIRECT_BASE_URL}\1", message)

    # username mentions: @johnsmith
    message = USERNAME_MENTION_PATTERN.sub(r"`\1`", message)

    return message

import re

from src.git import RepoId, PullRequestId


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


PULL_REQUEST_LINK_PATTERN = re.compile(r"(https://github.com/[\w\-_]+/[\w\-_]+/pull/\d+)")
def parse_pull_request_urls(text: str) -> list[PullRequestId]:
    urls = re.findall(PULL_REQUEST_LINK_PATTERN, text)
    return [PullRequestId.from_url(url) for url in urls]


REPO_LINK_PATTERN = re.compile(r"(https://github.com/[\w\-_]+/[\w\-_]+/?)")
def parse_repo_urls(text: str) -> list[RepoId]:
    urls = re.findall(REPO_LINK_PATTERN, text)
    return [RepoId.from_url(url) for url in urls]


IMPLICIT_ISSUE_PATTERN = re.compile(r"(?:^|[^\w`])(#\d+)(?:[^\w`]|$)")
def qualify_implicit_issues(message: str, repo_id: RepoId) -> str:
    """
    Finds implicit issue links (e.g. "#1234") and expands them to explicit issue links (e.g. "org/repo#1234")
    :param message:
    :param repo_id:
    :return:
    """
    return IMPLICIT_ISSUE_PATTERN.sub(rf" `{repo_id}\1`", message)

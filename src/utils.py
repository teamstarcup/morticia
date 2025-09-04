import re

REPO_LINK_PATTERN = re.compile(
    r"(https://github.com/[\w\-_]+/[\w\-_]+/?)"
)

PULL_REQUEST_LINK_PATTERN = re.compile(
    r"(https://github.com/[\w\-_]+/[\w\-_]+/pull/\d+)"
)

GITHUB_URL = "https://github.com/"

def get_pr_links_from_text(text: str) -> list[str]:
    return re.findall(PULL_REQUEST_LINK_PATTERN, text)


def get_repo_links_from_text(text: str) -> list[str]:
    return re.findall(REPO_LINK_PATTERN, text)


def repo_id_from_url(url: str) -> str:
    url = url.replace(GITHUB_URL, "")
    organization_name, repo_name, *_ = url.split("/")
    return f"{organization_name}/{repo_name}".lower()


def repo_base_url(url: str) -> str:
    """
    Takes a GitHub URL relevant to a repository and returns the URL to that repository.
    :param url:
    :return:
    """
    repo_full_name = repo_id_from_url(url)
    return f"{GITHUB_URL}{repo_full_name}"


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

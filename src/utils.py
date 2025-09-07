import re

from slugify import slugify

REPO_LINK_PATTERN = re.compile(
    r"(https://github.com/[\w\-_]+/[\w\-_]+/?)"
)

PULL_REQUEST_LINK_PATTERN = re.compile(
    r"(https://github.com/[\w\-_]+/[\w\-_]+/pull/\d+)"
)

GITHUB_URL = "https://github.com/"


class RepoId:
    org_name: str
    repo_name: str

    def __init__(self, org_name: str = "", repo_name: str = ""):
        self.org_name = org_name
        self.repo_name = repo_name

    def __repr__(self):
        return f"{self.org_name}/{self.repo_name}".lower()

    def url(self):
        return f"{GITHUB_URL}{str(self)}/"

    def slug(self):
        return slugify(str(self))

    @classmethod
    def from_url(cls, url: str):
        url = url.replace(GITHUB_URL, "")
        repo_id = RepoId()
        repo_id.org_name, repo_id.repo_name, *_ = url.split("/")
        repo_id.org_name = repo_id.org_name.lower()
        repo_id.repo_name = repo_id.repo_name.lower()
        return repo_id


class PullRequestId:
    org_name: str
    repo_name: str
    number: int

    def __repr__(self):
        return f"{self.org_name}/{self.repo_name}#{self.number}"

    def repo_id(self):
        repo_id = RepoId()
        repo_id.org_name = self.org_name
        repo_id.repo_name = self.repo_name
        return repo_id

    @classmethod
    def from_url(cls, url: str):
        url = url.replace(GITHUB_URL, "")
        pr_id = PullRequestId()
        pr_id.org_name, pr_id.repo_name, *_ = url.split("/")
        pr_id.org_name = pr_id.org_name.lower()
        pr_id.repo_name = pr_id.repo_name.lower()
        last_slash = url.rindex("/")
        pr_id.number = int(url[last_slash + 1:])
        return pr_id


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

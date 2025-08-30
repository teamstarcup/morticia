import logging

from github import Github, Auth

GITHUB_URL = "https://github.com/"

log = logging.getLogger(__name__)


class Morticia:
    def __init__(self, auth_token: str):
        self.auth = Auth.Token(auth_token)
        self.github = Github(auth=self.auth)

    @classmethod
    def repo_id_from_url(cls, url: str) -> tuple[str, str]:
        url = url.replace(GITHUB_URL, "")
        organization_name, repo_name, *_ = url.split("/")
        return (
            organization_name,
            repo_name,
        )

    @classmethod
    def issue_id_from_url(cls, url: str) -> int:
        last_slash = url.rindex("/")
        return int(url[last_slash + 1:])

    def get_pull_request(self, url: str):
        repo = self.github.get_repo("/".join(Morticia.repo_id_from_url(url)))
        pr_id = Morticia.issue_id_from_url(url)
        return repo.get_pull(pr_id)

    def close(self) -> None:
        self.github.close()

import logging
import os

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from github import Github, Auth
from slugify import slugify

GITHUB_URL = "https://github.com/"
REPOSITORIES_DIR = "./repositories"

log = logging.getLogger(__name__)


class Morticia:
    def __init__(self, auth_token: str):
        self.auth = Auth.Token(auth_token)
        self.github = Github(auth=self.auth)

    def close(self) -> None:
        self.github.close()

    @classmethod
    def repo_id_from_url(cls, url: str) -> tuple[str, str]:
        url = url.replace(GITHUB_URL, "")
        organization_name, repo_name, *_ = url.split("/")
        return (
            organization_name,
            repo_name,
        )

    @classmethod
    def repo_base_url(cls, url: str) -> str:
        """
        Takes a GitHub URL relevant to a repository and returns the URL to that repository.
        :param url:
        :return:
        """
        (organization_name, repo_name) = Morticia.repo_id_from_url(url)
        return f"{GITHUB_URL}{organization_name}/{repo_name}"

    @classmethod
    def repo_slug_from_url(cls, url: str) -> str:
        """
        Takes a GitHub URL relevant to a repository and returns an org-repo slug for that repository.
        :param url:
        :return:
        """
        (organization_name, repo_name) = Morticia.repo_id_from_url(url)
        return slugify(f"{organization_name}/{repo_name}")

    @classmethod
    def issue_id_from_url(cls, url: str) -> int:
        last_slash = url.rindex("/")
        return int(url[last_slash + 1:])

    @classmethod
    def pull_repo(cls, url: str):
        """
        Pulls (or clones) a repository's default branch down to the ``./repositories`` directory.
        """
        os.makedirs(REPOSITORIES_DIR, exist_ok=True)
        repo_url = Morticia.repo_base_url(url)
        repo_slug = Morticia.repo_slug_from_url(repo_url)
        repo_dir = f"{REPOSITORIES_DIR}/{repo_slug}"

        try:
            repo = porcelain.Repo(repo_dir)
            porcelain.pull(repo, force=True)
        except NotGitRepository as _:
            repo = porcelain.clone(repo_url, repo_dir)

        return repo

    def get_pull_request(self, url: str):
        repo = self.github.get_repo("/".join(Morticia.repo_id_from_url(url)))
        pr_id = Morticia.issue_id_from_url(url)
        return repo.get_pull(pr_id)
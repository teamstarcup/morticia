import logging
import os

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from github import Github, Auth, PullRequest
from github.Repository import Repository
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
    def repo_id_from_url(cls, url: str) -> str:
        url = url.replace(GITHUB_URL, "")
        organization_name, repo_name, *_ = url.split("/")
        return f"{organization_name}/{repo_name}"

    @classmethod
    def repo_base_url(cls, url: str) -> str:
        """
        Takes a GitHub URL relevant to a repository and returns the URL to that repository.
        :param url:
        :return:
        """
        repo_full_name = Morticia.repo_id_from_url(url)
        return f"{GITHUB_URL}{repo_full_name}"

    @classmethod
    def repo_slug_from_url(cls, url: str) -> str:
        """
        Takes a GitHub URL relevant to a repository and returns an org-repo slug for that repository.
        :param url:
        :return:
        """
        repo_full_name = Morticia.repo_id_from_url(url)
        return slugify(f"{repo_full_name}")

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

    def get_github_repo(self, url: str):
        repo_base_url = self.repo_id_from_url(url)
        return self.github.get_repo(repo_base_url)

    def get_pull_request(self, url: str):
        repo = self.get_github_repo(url)
        pr_id = Morticia.issue_id_from_url(url)
        return repo.get_pull(pr_id)

    def get_ancestors(self, url: str):
        """
        Search for a list of ancestor PRs for the given PR url.
        :param url:
        :return:
        """
        median_pr = self.get_pull_request(url)
        target_repo = self.get_github_repo(url)

        # gather list of files to search history
        relevant_file_paths: set[str] = set()
        for file in median_pr.get_files():
            match file.status:
                case "modified" | "changed" | "renamed" | "deleted":
                    relevant_file_paths.add(file.filename)

        # used for filtering ancestor PRs
        median_pr_time = median_pr.merged and median_pr.merged_at or median_pr.created_at

        # ancestors: list[str] = []
        for previous_pr in target_repo.get_pulls(state="closed"):
            if not previous_pr.merged:
                continue

            if previous_pr.merged_at > median_pr_time:
                continue

            if previous_pr.id == median_pr.id:
                continue

            for candidate_file in previous_pr.get_files():
                if candidate_file.filename in relevant_file_paths:
                    yield previous_pr.url
                    break

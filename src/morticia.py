import logging
import os

import sqlalchemy
from dulwich import porcelain
from dulwich.errors import NotGitRepository
from github import Github, Auth, UnknownObjectException
from sqlalchemy.orm import Session

from src.model import KnownPullRequest, KnownRepo, KnownFile, KnownFileChange
from src.utils import RepoId, PullRequestId

REPOSITORIES_DIR = "./repositories"

log = logging.getLogger(__name__)


class Morticia:
    def __init__(self, auth_token: str, session: Session):
        self.auth = Auth.Token(auth_token)
        self.github = Github(auth=self.auth)
        self.session = session

    def close(self) -> None:
        self.github.close()

    @classmethod
    def pull_repo(cls, repo_id: RepoId):
        """
        Pulls (or clones) a repository's default branch down to the ``./repositories`` directory.
        """
        os.makedirs(REPOSITORIES_DIR, exist_ok=True)
        repo_dir = f"{REPOSITORIES_DIR}/{repo_id.slug()}"

        try:
            repo = porcelain.Repo(repo_dir)
            porcelain.pull(repo, force=True)
        except NotGitRepository as _:
            repo = porcelain.clone(repo_id.url(), repo_dir)

        return repo

    def get_github_repo(self, repo_id: RepoId):
        return self.github.get_repo(str(repo_id))

    def get_pull_request(self, pr_id: PullRequestId):
        repo = self.get_github_repo(pr_id.repo_id())
        return repo.get_pull(pr_id.number)

    def index_repo(self, repo_id: RepoId):
        repo = self.get_github_repo(repo_id)

        # make sure this was inserted because foreignkey depends on it
        KnownRepo.as_unique(self.session, repo_id=str(repo_id))
        self.session.commit()

        highest_pull_request_id = repo.get_pulls(state="all", direction="desc").get_page(0)[0].number
        for i in range(highest_pull_request_id, 1, -1):
            # if self.session.execute(sqlalchemy.select(KnownPullRequest).where(KnownPullRequest.pull_request_id == i)).scalar():
            #     continue
            try:
                pull_request = repo.get_pull(i)
            except UnknownObjectException:
                continue

            known_pr = KnownPullRequest.as_unique(self.session, pull_request_id=pull_request.number, repo_id=str(repo_id))
            known_pr.update(pull_request)
            self.session.commit()

            # GitHub sends back an HTTP 422 error if we try to iterate changed files and there are none
            if pull_request.changed_files == 0:
                continue

            for file in pull_request.get_files():
                # make sure this was inserted because foreignkey depends on it
                KnownFile.as_unique(self.session, repo_id=str(repo_id), file_path=file.filename)

                known_file_change = KnownFileChange.as_unique(self.session, pull_request_id=pull_request.number, repo_id=str(repo_id), file_path=file.filename)
                known_file_change.update(file)

            self.session.commit()
            # break

    def get_upstream_merge_prs(self, repo_id: RepoId):
        """
        Returns a list of KnownPullRequests, which are not necessarily merged.
        :param repo_id:
        :return:
        """
        statement = (sqlalchemy.select(KnownFileChange, KnownPullRequest)
                     .where(KnownFileChange.repo_id == str(repo_id))
                     .where(KnownFileChange.file_path == "Resources/Changelog/Changelog.yml")
                     .join(KnownPullRequest, onclause=KnownFileChange.pull_request))
        known_file_changes = self.session.execute(statement).scalars().all()
        return list(map(lambda change: change.pull_request, known_file_changes))

    HIGH_FREQUENCY_FILES = {
        "Resources/Prototypes/Entities/Structures/Machines/lathe.yml",
        "Resources/Prototypes/tags.yml",
        "Resources/Prototypes/Loadouts/loadout_groups.yml",
        "Resources/Prototypes/Entities/Mobs/NPCs/animals.yml",
        "Resources/Prototypes/Entities/Objects/Fun/toys.yml",
        "Resources/Prototypes/Loadouts/Miscellaneous/trinkets.yml",
        "Resources/Prototypes/_Impstation/Loadouts/Miscellaneous/trinkets.yml",
    }

    def get_ancestors(self, pr_id: PullRequestId):
        """
        Search for a list of ancestor PRs for the given pull request.
        :param pr_id:
        :return:
        """
        median_pr = self.get_pull_request(pr_id)
        repo_id = pr_id.repo_id()

        # gather list of files to search history
        relevant_file_paths: set[str] = set()
        for file in median_pr.get_files():
            if file.filename in Morticia.HIGH_FREQUENCY_FILES:
                continue
            match file.status:
                case "modified" | "changed" | "renamed" | "deleted":
                    relevant_file_paths.add(file.filename)

        # used for filtering ancestor PRs
        median_pr_time = median_pr.merged and median_pr.merged_at or median_pr.created_at
        median_pr_time = median_pr_time.replace(tzinfo=None)

        known_upstream_merges = self.get_upstream_merge_prs(repo_id)

        ancestors: set[KnownPullRequest] = set()
        for relevant_file_path in relevant_file_paths:
            known_file_changes = self.session.execute(
                sqlalchemy.select(KnownFileChange)
                .where(KnownFileChange.repo_id == str(repo_id))
                .filter(
                    ((KnownFileChange.file_path == relevant_file_path) | (KnownFileChange.previous_file_path == relevant_file_path))
                )
            )

            for known_file_change in known_file_changes.scalars():
                known_pull_request = self.session.execute(sqlalchemy.select(KnownPullRequest).filter((KnownPullRequest.repo_id == str(repo_id)) & (KnownPullRequest.pull_request_id == known_file_change.pull_request_id))).scalar()

                if not known_pull_request.merged:
                    continue

                if known_pull_request.merged_at >= median_pr_time:
                    continue

                if known_pull_request in known_upstream_merges:
                    continue

                ancestors.add(known_pull_request)

        def sort_by_oldest(element: KnownPullRequest):
            return element.merged_at
        ancestors: list[KnownPullRequest] = list(ancestors)
        ancestors.sort(key=sort_by_oldest)

        ancestor_links = []
        for ancestor in ancestors:
            ancestor_links.append(f"#{ancestor.pull_request_id} - {ancestor.title}")

        return ancestor_links

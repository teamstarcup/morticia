import asyncio
import logging
import time
from enum import Enum
from typing import Optional

import discord
import sqlalchemy
from github import Github, Auth, UnknownObjectException
from sqlalchemy.orm import Session

from src.model import KnownPullRequest, KnownRepo, KnownFile, KnownFileChange, ProjectLatestAddition
from .git import LocalRepo, RepoId, PullRequestId, MergeConflictsException
from .pubsub import Publisher, MessageEvent
from .status import StatusMessage
from .ui.pages import MergeConflictsPaginator
from .utils import qualify_implicit_issues, parse_pull_request_urls, pretty_duration

log = logging.getLogger(__name__)


HOME_REPO_ID = RepoId("teamstarcup", "starcup")


class PortingMethod(Enum):
    PATCH = 0,
    CHERRY_PICK = 1,


class Project:
    initial_pull_request_opened: bool = False

    def __init__(self, thread: discord.Thread, work_repo: LocalRepo, github: Github, session: Session):
        self.thread = thread
        self.publisher = Publisher()
        self.work_repo = work_repo
        self.github = github
        self.session = session

        self.branch = None
        self.status_message = StatusMessage(thread)

        self._benchmark_start = None

    def __enter__(self):
        self.publisher.subscribe(self.status_message)
        self.work_repo.publisher = self.publisher

    def __exit__(self, exc_type, exc_value, traceback):
        self.publisher.unsubscribe(self.status_message)
        self.work_repo.publisher = None

    async def _get_github_repo(self, repo_id: RepoId):
        return self.github.get_repo(str(repo_id))

    async def _get_pull_request(self, pull_request_id: PullRequestId):
        repo = await self._get_github_repo(pull_request_id.repo_id())
        return repo.get_pull(pull_request_id.number)

    async def _get_github_username(self):
        return self.github.get_user().login

    async def _get_github_token(self):
        return self.github

    async def get_initial_pull_request(self):
        """
        Returns the initial pull request which was ported in this project.
        :return:
        """
        original_message = await self.thread.fetch_message(self.thread.id)
        pull_request_id = parse_pull_request_urls(original_message.content).pop()
        return pull_request_id

    async def get_branch(self, or_name: Optional[str] = None):
        if self.initial_pull_request_opened and not self.branch:
            pull_request_id = await self.get_initial_pull_request()
            self.branch = pull_request_id.slug()
        elif not self.branch:
            self.branch = or_name
        return self.branch

    async def prepare_repo(self, token: str):
        """
        Switches to the main branch and synchronizes with the home repository's main branch. Also resets previous
        merging state and updates the access token in the remote tracking url.

        - clears previous merge resolution state
        - fetches remote ``origin``, checks out ``main``, and resets ``HEAD`` to ``origin/HEAD``
        - updates tracking url for remote ``origin`` to use the provided token
        - tracks ``teamstarcup/starcup`` as ``teamstarcup-starcup``
        - fetches remote ``teamstarcup-starcup``, checks out ``main``, and resets ``HEAD`` to ``teamstarcup-starcup/HEAD``
        - pushes ``main`` to ``origin/main``

        :param token:
        :return:
        """
        await self.work_repo.reset_hard("HEAD")  # clear any previous bad state
        await self.work_repo.sync_branch_with_remote("origin", await self.work_repo.default_branch())

        remote_url = await self.work_repo.get_remote_url("origin")
        if token not in remote_url:
            remote_url = remote_url.replace("://github.com", f"://{self._get_github_username()}:{token}@github.com")
            await self.work_repo.set_remote_url("origin", remote_url)

        await self.work_repo.track_remote(HOME_REPO_ID)

        default_branch = await self.work_repo.default_branch(HOME_REPO_ID)
        await self.work_repo.sync_branch_with_remote(HOME_REPO_ID.slug(), default_branch)

    async def _get_project_state(self):
        """
        Obtains the state for the latest pull request ported in this project.
        :return: ProjectLatestAddition
        """
        branch = await self.get_branch()
        project_latest_addition = self.session.execute(
            sqlalchemy.select(ProjectLatestAddition).where(ProjectLatestAddition.branch == branch)
        ).scalar()

        if project_latest_addition is None:
            project_latest_addition = ProjectLatestAddition()
            project_latest_addition.branch = branch
            self.session.add(project_latest_addition)

        return project_latest_addition

    async def _select_porting_method(self, pull_request_id: PullRequestId):
        """
        Determines the method for porting a pull request.

        Pull requests that end in a commit with one parent will be cherry-picked.
        Commits with multiple parents and unmerged pull requests are trickier, so we want to grab the
        patch files for every commit in that branch to apply them one at a time.
        :param pull_request_id:
        :return:
        """
        target_pull_request = await self._get_pull_request(pull_request_id)

        if not target_pull_request.is_merged():
            return PortingMethod.PATCH

        target_repo_github = await self._get_github_repo(pull_request_id.repo_id())
        target_commit = target_repo_github.get_commit(target_pull_request.merge_commit_sha).commit
        if len(target_commit.parents) > 1:
            return PortingMethod.PATCH

        return PortingMethod.CHERRY_PICK

    async def _fetch_remote_for_pull_request(self, pull_request_id: PullRequestId):
        target_repo_id = pull_request_id.repo_id()
        await self.work_repo.track_remote(target_repo_id)
        await self.work_repo.fetch(target_repo_id)

    async def _finish_adding_pull_request(self, pull_request_id: PullRequestId):
        await self.work_repo.push("origin", await self.get_branch())
        project_state = await self._get_project_state()
        project_state.pull_request_id = str(pull_request_id)
        self.session.commit()

        duration = int(time.time() - self._benchmark_start)
        await self.publisher.publish(MessageEvent("comment", f"Completed in {pretty_duration(duration)}"))
        await self.status_message.flush()

    async def add_pull_request(self, pull_request_id: PullRequestId):
        """
        Attempts to port commit[s] from a pull request into this project.

        Raises :class:`MergeConflictsException` upon encountering conflicts. Handle them with the information
        provided from the exception and call :meth:`continue_merge`.
        :param pull_request_id:
        :return:
        """
        self._benchmark_start = time.time()

        target_pull_request = await self._get_pull_request(pull_request_id)

        # add target repo as remote to local work repo
        await self._fetch_remote_for_pull_request(pull_request_id)

        # create new branch in local work repo
        await self.work_repo.checkout(await self.get_branch(pull_request_id.slug()))

        # TODO: Migrate file names from existing commits authored before the new commit/s
        # For each commit on this branch not reachable from `origin/HEAD`
        # For each file in commit
        # rename_info: FileRenameInfo = await self.get_eventual_file_name(file_name, from_commit, to_commit)
        # if rename_info: rename file
        # if any files were renamed, stage files and author a commit

        method = await self._select_porting_method(pull_request_id)
        if method == PortingMethod.PATCH:
            await self.work_repo.apply_patch_from_url_conflict_resolving(target_pull_request.patch_url)
        else:
            await self.work_repo.cherry_pick(target_pull_request.merge_commit_sha)

        await self._finish_adding_pull_request(pull_request_id)

    async def _interactive_conflict_resolution(self, interaction: discord.Interaction, exception: MergeConflictsException):
        while True:
            paginator = MergeConflictsPaginator(exception.conflicts)
            resume = await paginator.respond(interaction, target=self.thread)

            if not resume:
                await paginator.disable(include_custom=True, page="Cancelled.")
                return False

            await paginator.disable(include_custom=True, page="All conflicts resolved!")

            await self.status_message.write_comment("Resolving conflicts...")
            for conflict in exception.conflicts:
                await conflict.resolve()

            try:
                await self.work_repo.continue_merge(exception.command)
                return True
            except MergeConflictsException as e2:
                exception = e2

    async def add_pull_request_interactive(self, pull_request_id: PullRequestId, interaction: discord.Interaction):
        """
        Attempts to port commit[s] from a pull request into this project. If there are merge conflicts, the user will
        be prompted to resolve conflicts manually until successful.
        :param pull_request_id:
        :param interaction:
        :return: true if the process was completed successfully
        """
        try:
            await self.add_pull_request(pull_request_id)
        except MergeConflictsException as exception:
            success = await self._interactive_conflict_resolution(interaction, exception)
            if success:
                return False
            await self._finish_adding_pull_request(pull_request_id)

        return True

    async def create_pull_request(self, title: str, pull_request_id: PullRequestId, draft: bool = False):
        target_pull_request = await self._get_pull_request(pull_request_id)
        body = f"Port of {target_pull_request}"
        body += "\n\n"
        body += "## Quote\n"
        body += target_pull_request.body

        body = qualify_implicit_issues(body, pull_request_id.repo_id())

        home_repo_github = await self._get_github_repo(HOME_REPO_ID)
        new_pull_request = home_repo_github.create_pull(
            await self.work_repo.default_branch(HOME_REPO_ID),
            f"{await self._get_github_username()}:{await self.get_branch()}",
            body=body,
            title=title,
            draft=draft,
        )

        self.initial_pull_request_opened = True
        return new_pull_request


class Morticia:
    def __init__(self, auth_token: str, session: Session):
        self.auth = Auth.Token(auth_token)
        self.github = Github(auth=self.auth)
        self.session = session
        self.home_repo_id = RepoId("teamstarcup", "starcup")
        self.work_repo_id = RepoId("teamstarcup-bot", "starcup")

    def close(self) -> None:
        self.github.close()

    def get_github_repo(self, repo_id: RepoId):
        return self.github.get_repo(str(repo_id))

    def get_pull_request(self, pr_id: PullRequestId):
        repo = self.get_github_repo(pr_id.repo_id())
        return repo.get_pull(pr_id.number)

    async def index_repo(self, repo_id: RepoId):
        repo = self.get_github_repo(repo_id)

        # make sure this was inserted because foreignkey depends on it
        KnownRepo.as_unique(self.session, repo_id=str(repo_id))
        self.session.commit()

        highest_pull_request_id = repo.get_pulls(state="all", direction="desc").get_page(0)[0].number
        for i in range(highest_pull_request_id, 1, -1):
            # if self.session.execute(sqlalchemy.select(KnownPullRequest).where(KnownPullRequest.pull_request_id == i and KnownPullRequest.repo_id == repo_id)).scalar():
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
                # await asyncio.sleep(1)

            await asyncio.sleep(1)

            self.session.commit()
            # break

    def get_upstream_merge_prs(self, repo_id: Optional[RepoId] = None):
        """
        Returns a list of KnownPullRequests, which are not necessarily merged.
        :param repo_id:
        :return:
        """
        statement = sqlalchemy.select(KnownFileChange, KnownPullRequest)
        statement = statement.join(KnownPullRequest, KnownFileChange.pull_request)
        statement = statement.where(KnownFileChange.file_path == "Resources/Changelog/Changelog.yml")
        if repo_id is not None:
            statement = statement.filter(KnownFileChange.repo_id == str(repo_id))
            statement = statement.filter(KnownPullRequest.repo_id == str(repo_id))
        print(statement)
        known_file_changes = self.session.execute(statement).scalars().all()
        return list(map(lambda change: change.pull_request, known_file_changes))

    def search_for_file_changes(self, path: str, repo_id: Optional[RepoId] = None, merged_only: bool = True, ignore_upstream_merges: bool = True):
        """
        Returns a list of KnownPullRequests that modify the given file path.
        :param path: path to the file to search for changes
        :param repo_id: the repository, if any, to exclusively search for changes
        :param merged_only: ignore unmerged pull requests
        :param ignore_upstream_merges: ignore pull requests that modify ``Resources/Changelog/Changelog.yml``
        :return:
        """
        statement = sqlalchemy.select(KnownFileChange, KnownPullRequest)
        statement = statement.filter(KnownFileChange.file_path == path)
        if repo_id is not None:
            statement = statement.filter(KnownFileChange.repo_id == str(repo_id), KnownPullRequest.repo_id == str(repo_id))
        if merged_only:
            statement = statement.filter(KnownPullRequest.merged)
        statement = statement.join(KnownPullRequest, KnownFileChange.pull_request)
        print(statement)
        known_file_changes = self.session.execute(statement).scalars().all()
        pull_requests = list(map(lambda change: change.pull_request, known_file_changes))

        if ignore_upstream_merges:
            upstream_merges = self.get_upstream_merge_prs(repo_id)
            for upstream_merge in upstream_merges:
                for pull_request in pull_requests:
                    if pull_request.pull_request_id == upstream_merge.pull_request_id:
                        pull_requests.remove(pull_request)
                        break

        return pull_requests


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

    def get_descendants(self, pr_id: PullRequestId):
        """
        Search for a list of descendant PRs for the given pull request.
        :param pr_id:
        :return:
        """
        median_pr = self.get_pull_request(pr_id)
        repo_id = pr_id.repo_id()

        # gather list of files to search history
        relevant_file_paths: set[str] = set()
        for file in median_pr.get_files():
            match file.status:
                case "added":
                    relevant_file_paths.add(file.filename)

        median_pr_time = median_pr.merged and median_pr.merged_at or median_pr.created_at
        median_pr_time = median_pr_time.replace(tzinfo=None)

        known_upstream_merges = self.get_upstream_merge_prs(repo_id)

        descendants: set[KnownPullRequest] = set()
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

                if known_pull_request.merged_at <= median_pr_time:
                    continue

                if known_pull_request in known_upstream_merges:
                    continue

                descendants.add(known_pull_request)

        def sort_by_oldest(element: KnownPullRequest):
            return element.merged_at
        descendants: list[KnownPullRequest] = list(descendants)
        descendants.sort(key=sort_by_oldest)

        descendant_links = []
        for descendant in descendants:
            descendant_links.append(f"#{descendant.pull_request_id} - {descendant.title}")

        return descendant_links

    def eventual_file_name(self, file_path: str, repo_id: RepoId):
        """
        Finds the most recent path for a given file
        :param file_path:
        :param repo_id:
        :return:
        """
        statement = sqlalchemy.select(KnownFileChange).where(KnownFileChange.repo_id == str(repo_id), KnownFileChange.previous_file_path == file_path)
        known_file_change: KnownFileChange = self.session.execute(statement).scalar()
        return known_file_change and known_file_change.file_path or None

    def project_state(self, branch: str) -> ProjectLatestAddition:
        """
        Obtains the latest pull request addition to an existing port project.
        :param branch:
        :return:
        """
        project_latest_addition = self.session.execute(
            sqlalchemy.select(ProjectLatestAddition).where(ProjectLatestAddition.branch == branch)
        ).scalar()

        if project_latest_addition is None:
            project_latest_addition = ProjectLatestAddition()
            project_latest_addition.branch = branch
            self.session.add(project_latest_addition)

        return project_latest_addition

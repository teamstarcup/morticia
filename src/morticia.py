import asyncio
import logging

import discord
import sqlalchemy
from github import Github, Auth, UnknownObjectException
from sqlalchemy.orm import Session

from src.model import KnownPullRequest, KnownRepo, KnownFile, KnownFileChange
from .git import LocalRepo, RepoId, PullRequestId, GitCommandException, MergeConflictsException
from .status import StatusMessage
from .ui.pages import MergeConflictsPaginator
from .utils import obscure_references, qualify_implicit_issues

log = logging.getLogger(__name__)


class Morticia:
    def __init__(self, auth_token: str, session: Session):
        self.auth = Auth.Token(auth_token)
        self.github = Github(auth=self.auth)
        self.session = session
        self.home_repo_id = RepoId("teamstarcup", "starcup")
        self.work_repo_id = RepoId("teamstarcup-bot", "starcup")

    def close(self) -> None:
        self.github.close()

    def github_username(self):
        return self.github.get_user().login

    def get_github_repo(self, repo_id: RepoId):
        return self.github.get_repo(str(repo_id))

    def get_pull_request(self, pr_id: PullRequestId):
        repo = self.get_github_repo(pr_id.repo_id())
        return repo.get_pull(pr_id.number)

    async def get_local_repo(self, repo_id: RepoId):
        """
        Obtains an existing LocalRepo object, or clones it if it does not yet exist.
        :param repo_id:
        :return:
        """
        return await LocalRepo.open(repo_id, self.get_github_repo(repo_id).default_branch)

    async def sync_work_repo(self, work_repo: LocalRepo):
        remote_url = await work_repo.get_remote_url("origin")
        remote_url = remote_url.replace("://github.com", f"://{self.github_username()}:{self.auth.token}@github.com")
        await work_repo.set_remote_url("origin", remote_url)

        await work_repo.abort_merge()  # clear previous bad state
        await work_repo.abort_patch()
        await work_repo.abort_cherry_pick()
        await work_repo.sync_branch_with_remote("origin", work_repo.default_branch)

        await work_repo.track_and_fetch_remote(self.home_repo_id)

        await work_repo.sync_branch_with_remote(self.home_repo_id.slug(), "main")

        await work_repo.push("origin", force=True)

    async def start_port(self, pr_id: PullRequestId, interaction: discord.Interaction):
        status = StatusMessage(interaction)

        await status.write_comment(f"Opening {self.work_repo_id} ...")
        work_repo = await self.get_local_repo(self.work_repo_id)
        work_repo.status = status

        await status.write_comment(f"Synchronizing with {self.home_repo_id}")
        await self.sync_work_repo(work_repo)

        # add target repo as remote to local work repo
        target_repo_id = pr_id.repo_id()
        await work_repo.track_and_fetch_remote(target_repo_id)

        # create new branch in local work repo
        branch_name = pr_id.slug()
        await work_repo.checkout(branch_name)

        target_pull_request = self.get_pull_request(pr_id)
        naive_resolution_applied = False
        try:
            # cherry-picks for squashed & merged PRs, patches for everything else
            target_repo_github = self.get_github_repo(target_repo_id)
            target_commit = target_repo_github.get_commit(target_pull_request.merge_commit_sha).commit
            if target_pull_request.is_merged() and len(target_commit.parents) == 1:
                await work_repo.cherry_pick(target_pull_request.merge_commit_sha)
            else:
                naive_resolution_applied = await work_repo.apply_patch_from_url_conflict_resolving(target_pull_request.patch_url)
        except MergeConflictsException as e:
            while True:
                future = asyncio.get_running_loop().create_future()
                paginator = MergeConflictsPaginator(e.conflicts, future)
                await paginator.respond(interaction)
                await future

                await paginator.disable(include_custom=True, page="All conflicts resolved!")
                if future.cancelled():
                    return

                await status.write_comment("Resolving conflicts...")
                for conflict in e.conflicts:
                    await conflict.resolve()

                try:
                    await work_repo.continue_merge(e.command)
                    break
                except MergeConflictsException as e2:
                    e = e2

            # work_repo.abort_patch()
            # return
        except GitCommandException as e:
            # work_repo.abort_patch()
            await status.write_error(f"stdout: {e.stdout}\n\nstderr: {e.stderr}")
            await interaction.respond(f"{interaction.user.mention} Failed!")
            return

        if naive_resolution_applied:
            await status.write_comment("WARNING: Some merge conflicts were solved with naive conflict resolution!")

        await status.write_comment("I would have submitted a pull request, but this was a dry run.")

        # await work_repo.push("origin", branch_name)
        #
        # body = f"Port of {pr_id}"
        # body += "\n\n"
        # body += "## Quote\n"
        # body += target_pull_request.body
        #
        # body = qualify_implicit_issues(body, target_repo_id)
        # body = obscure_references(body)
        #
        # home_repo_github = self.get_github_repo(self.home_repo_id)
        # new_pr = home_repo_github.create_pull(
        #     "main",
        #     f"{self.github_username()}:{branch_name}",
        #     body=body,
        #     title=target_pull_request.title,
        #     draft=naive_resolution_applied
        # )
        #
        # await interaction.respond(f"Complete: {new_pr.html_url}")

    def index_repo(self, repo_id: RepoId):
        repo = self.get_github_repo(repo_id)

        # make sure this was inserted because foreignkey depends on it
        KnownRepo.as_unique(self.session, repo_id=str(repo_id))
        self.session.commit()

        highest_pull_request_id = repo.get_pulls(state="all", direction="desc").get_page(0)[0].number
        for i in range(1639, 1, -1):
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

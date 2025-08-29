import logging
import os
import re
import subprocess

import git
import github.GithubException
import requests
from github import Github, Auth, Repository, PullRequest
from git import Repo
from slugify import slugify

GITHUB_URL = "https://github.com/"
MODIFIED_NONEXTANT_FILE_MESSAGE = "deleted in HEAD and modified in"

EXPLICIT_ISSUE_PATTERN = re.compile(r"([A-Za-z0-9_\.-]+\/[A-Za-z0-9_\.-]+#\d+)")
IMPLICIT_ISSUE_PATTERN = re.compile(r"(?:^|[^\w`])(#\d+)(?:[^\w`]|$)")

log = logging.getLogger(__name__)


def repo_id_from_url(url: str) -> tuple[str, str]:
    url = url.replace(GITHUB_URL, "")
    organization_name, repo_name, *_ = url.split("/")
    return (
        organization_name,
        repo_name,
    )


def issue_id_from_url(url: str) -> int:
    last_slash = url.rindex("/")
    return int(url[last_slash + 1 :])


# e.g. fixes #491 -> fixes space-wizards/space-station-14#491
def qualify_implicit_issue_links(haystack: str, absolute_repo_id: str) -> str:
    return IMPLICIT_ISSUE_PATTERN.sub(rf" {absolute_repo_id}\1 ", haystack)


def build_comment(force_created_files: list[str]):
    comment_body = ""

    # list files affected by improvised conflict resolution
    if len(force_created_files) > 0:
        comment_body += "## Inducted files\n"
        comment_body += (
            "The pull request modified files which we did not already have. I have added them to "
            "resolve merge conflicts.\n"
        )
        comment_body += "```\n"
        for new_file in force_created_files:
            comment_body += f"{new_file}\n"
        comment_body += "```\n"
        comment_body += "\n"
        comment_body += (
            "Please remember to check these files for out-of-scope additions.\n"
        )
        comment_body += "\n"

    # - generate list of antecedent PRs from modified files
    # - generate list of succedent PRs that modify files which were introduced here

    comment_body += """
## Review checklist
- [ ] License compatibility and adherence
  - [ ] Code
  - [ ] Sprites
  - [ ] Audio
- [ ] Namespaces
  - [ ] Comment changes in upstream namespaces
  - [ ] New additions in partitioned namespaces
  - [ ] Namespace adheres to source's current standard
- [ ] Audio normalization and bit rate
- [ ] Out-of-scope additions
- [ ] Changelog convention"""
    return comment_body


def add_remote(local_repo: Repo, target_repo: Repository) -> git.Remote:
    remote_slug = target_repo.full_name.replace("/", "-")
    remote_url = target_repo.clone_url
    new_remote = git.Remote(local_repo, remote_slug)
    if new_remote.exists():
        log.info(f"Changing remote {remote_slug} url to {remote_url}")
        new_remote.set_url(remote_url)
    else:
        new_remote.create(local_repo, remote_slug, remote_url)
        new_remote.set_url(remote_url)
    return new_remote


class MergeConflictException(Exception):
    def __init__(self, message: str, stderr: str):
        self.message = message
        self.stderr = stderr

    pass


class Starcatcher:
    def __init__(self, auth_token: str, home_repo_id: str, username: str, email: str):
        self.auth = Auth.Token(auth_token)
        self.github = Github(auth=self.auth)
        self.home_github = self.github.get_repo(home_repo_id)

        self.username = username
        self.email = email

        self.work_repo_id = f"{username}/{self.home_github.name}"
        try:
            self.work_github = self.github.get_repo(f"{self.work_repo_id}")
        except github.UnknownObjectException:
            log.info(f"Forking home repo {home_repo_id} into {self.work_repo_id}")
            self.work_github = self.github.get_user().create_fork(self.home_github)

        # if no local work repo, clone it
        self.work_local: Repo
        repo_dir = "work"
        if not os.path.exists(repo_dir):
            log.info("Cloning repo...")
            clone_url = self.work_github.clone_url
            clone_url = clone_url.replace(
                "://github.com", f"://{self.username}:{auth_token}@github.com"
            )
            self.work_local = Repo.clone_from(clone_url, repo_dir)
        else:
            self.work_local = Repo(repo_dir)

        self.work_local.config_writer().set_value(
            "user", "name", self.username
        ).release()
        self.work_local.config_writer().set_value("user", "email", self.email).release()

    def get_pull_request(self, url: str):
        repo = self.github.get_repo("/".join(repo_id_from_url(url)))
        pr_id = issue_id_from_url(url)
        return repo.get_pull(pr_id)

    @classmethod
    def create_branch(cls, repo: Repo, branch_name: str, from_branch_name: str):
        # repo.git.branch(branch_name)
        # repo.git.checkout(branch_name)
        repo.git.branch(branch_name, from_branch_name)
        repo.git.checkout(branch_name)
        rem_ref = git.RemoteReference(repo, f"refs/remotes/origin/{branch_name}")
        repo.head.reference.set_tracking_branch(rem_ref)

    def cherry_pick(self, commit_sha: str):
        try:
            self.work_local.git.cherry_pick("-m 1", "-x", commit_sha)
        except git.GitCommandError as e:
            if "error: could not apply" not in e.stderr:
                self.work_local.git.cherry_pick("--abort")
                raise e

            status_message = e.stdout
            status_message = status_message.replace("stdout: '", "")
            status_message = status_message[:-1]

            raise MergeConflictException(status_message, e.stderr)

    def port(self, url: str) -> PullRequest:
        # parse target repo from url
        (organization_name, repo_name) = repo_id_from_url(url)
        target_repo_id = "/".join((organization_name, repo_name))
        pull_id = issue_id_from_url(url)

        # fetch pr details
        log.info(f"Fetching PR details from {url}")
        target_repo = self.github.get_repo(target_repo_id)
        pull_request = target_repo.get_pull(pull_id)

        # update main branch
        default_branch_name = self.home_github.default_branch
        log.info(f"Updating local repo {default_branch_name} branch...")
        self.work_local.git.checkout(default_branch_name)
        # self.work_local.remotes["origin"].pull()

        # update main branch from home repo main
        home_remote = add_remote(self.work_local, self.home_github)
        self.work_local.git.pull(home_remote.name, default_branch_name)
        self.work_local.git.submodule("update", "--init", "--recursive")

        # add target repo as remote
        new_remote = add_remote(self.work_local, target_repo)

        # fetch target remote
        log.info("Fetching target...")
        new_remote.fetch()

        # new branch
        branch_name = slugify(f"autoport-pr-{organization_name}-{repo_name}-{pull_id}")
        if branch_name in self.work_local.branches:
            log.info(f"Branch {branch_name} exists, deleting...")
            git.Head.delete(self.work_local, branch_name, force=True)

        log.info(f"Creating branch {branch_name}")
        self.create_branch(self.work_local, branch_name, default_branch_name)

        # cherry-pick merge commit
        force_created_files = []
        used_patch = False
        conflicted = False
        try:
            if not pull_request.merged:
                # commit_sha = pull_request.get_commits().get_page(0).pop().sha
                # log.info(f"No merge commit yet, merging first commit hash {commit_sha}")

                patch_url = pull_request.patch_url
                r = requests.get(patch_url)
                with open('tmp.patch', 'wb') as f:
                    f.write(r.content)
                self.work_local.git.am('../tmp.patch', '--keep-non-patch')
                used_patch = True
            else:
                commit_sha = pull_request.merge_commit_sha
                self.cherry_pick(commit_sha)
        except MergeConflictException as e:
            # if MODIFIED_NONEXTANT_FILE_MESSAGE not in e.message:
            #     log.error(f"Unable to apply cherry-pick: {e.stderr}")
            #     raise e

            # log.info(f"Encountered merge conflict(s), attempting to solve them...")
            # log.error(e)
            # conflicts = e.message.split("CONFLICT (modify/delete):")
            # for conflict in conflicts:
            #     if MODIFIED_NONEXTANT_FILE_MESSAGE not in conflict:
            #         continue
            #
            #     path = conflict.split(MODIFIED_NONEXTANT_FILE_MESSAGE)[0].strip()
            #     log.info(f"Accepting their changes: {path}")
            #     self.work_local.git.add(path)
            #     force_created_files.append(path)

            conflicted = True
            self.work_local.git.add("--all")

            try:
                self.work_local.git.cherry_pick("--continue")
            except git.GitCommandError as e2:
                log.error(f"Unable to apply cherry-pick, aborting: {e2.stdout}")
                self.work_local.git.cherry_pick("--abort")
                raise MergeConflictException(str(e2))

        # amend commit message to relocate implicit issue references
        if not used_patch:
            merge_commit_message = target_repo.get_commit(
                pull_request.merge_commit_sha
            ).commit.message
            merge_commit_message = qualify_implicit_issue_links(
                merge_commit_message, target_repo_id
            ).replace("#", "$")
            log.info(merge_commit_message)
            self.work_local.git.commit(f"--amend", f"-m {merge_commit_message}")

        self.work_local.remotes["origin"].push(force=True)

        pr_title = f'Port "{pull_request.title}"'
        target_pr_id = f"{organization_name}/{repo_name}#{pull_id}"
        pr_body = f"Port of [{target_pr_id}](https://redirect.github.com/{target_pr_id.replace('#', '/pull/')})"
        pr_body += "\n\n"

        # unlink hyperlinks
        pull_request_body = pull_request.body.replace(
            "http://", "http:&zwnj;//"
        ).replace("https://", "https:&zwnj;//")

        # unlink issues/pull requests
        pull_request_body = EXPLICIT_ISSUE_PATTERN.sub(r"`\1`", pull_request_body)

        # prepend repo origin to issues
        pull_request_body = IMPLICIT_ISSUE_PATTERN.sub(
            rf" `{organization_name}/{repo_name}\1` ", pull_request_body
        )

        # modify changelog to include authors
        pull_request_author = pull_request.user.login
        changelog_start_pattern = re.compile(r"\n:cl:")
        pull_request_body = changelog_start_pattern.sub(
            f"\n:cl: {pull_request_author}", pull_request_body
        )

        pr_body += "## Quote\n"
        pr_body += pull_request_body
        pr_body += "\n\n"

        new_pull = self.home_github.create_pull(
            default_branch_name,
            f"{self.username}:{branch_name}",
            title=pr_title,
            body=pr_body,
            draft=conflicted,
        )

        # comment relevant trailing information
        comment_body = build_comment(force_created_files)
        new_pull.as_issue().create_comment(comment_body)

        return new_pull

    def close(self) -> None:
        self.github.close()

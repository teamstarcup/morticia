import asyncio
import os
from typing import Optional, Union

import requests
from slugify import slugify

GITHUB_URL = "https://github.com/"

REPOSITORIES_DIR = "./repositories"
os.makedirs(REPOSITORIES_DIR, exist_ok=True)


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

    def slug(self):
        return f"{self.org_name}-{self.repo_name}-{self.number}"

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


class CommandException(BaseException):
    stdout: str
    stderr: str
    exit_code: int

    def __init__(self, stdout: str, stderr: str, exit_code: int):
        super().__init__()
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class GitCommandException(CommandException):
    def __init__(self, exception: CommandException):
        super().__init__(exception.stdout, exception.stderr, exception.exit_code)


class LocalRepo:
    default_branch: str

    def __init__(self, path: str, repo_id: RepoId, default_branch: str):
        self.path = path
        self.repo_id = repo_id
        self.default_branch = default_branch

    async def subprocess(self, cmd: str, working_directory: Optional[Union[str, bytes, os.PathLike]] = None):
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_directory or self.path,
        )
        stdout, stderr = await proc.communicate()
        stdout = stdout.decode("cp1252")
        stderr = stderr.decode("cp1252")

        if proc.returncode != 0:
            raise CommandException(stdout, stderr, proc.returncode)

        return stdout, proc.returncode

    async def git(self, cmd: str, working_directory: Optional[Union[str, bytes, os.PathLike]] = None):
        try:
            stdout, return_code = await self.subprocess(f"git {cmd}", working_directory)
        except CommandException as e:
            raise GitCommandException(e)
        return stdout, return_code

    async def abort_merge(self):
        try:
            await self.git("merge --abort")
        except GitCommandException as e:
            if "There is no merge to abort" not in e.stderr:
                raise e

    async def abort_patch(self):
        try:
            await self.git("am --abort")
        except GitCommandException as e:
            if "Resolve operation not in progress, we are not resuming." not in e.stderr:
                raise e

    async def apply_patch(self, patch: str, extra_options: str = ""):
        await self.git(f"am {patch} --keep-non-patch {extra_options}")

    async def apply_patch_conflict_resolving(self, patch: str, extra_options: str = ""):
        """
        Attempts to apply a patch file with naive conflict resolution. If git encounters any modified files that
        don't exist in the repository, we simply include those files wholesale.
        :param patch: Path to the patch file
        :param extra_options: Extra arguments to be passed to ``git am``
        :return: ``True`` if naive conflict resolution was applied
        """
        naive_resolution_applied = False
        try:
            await self.apply_patch(patch, f"--3way {extra_options}")
        except GitCommandException as e:
            while "deleted in HEAD and modified in" in e.stdout:
                # let's get lucky
                naive_resolution_applied = True
                await self.git("add --all")
                try:
                    await self.git("am --continue")
                    break
                except GitCommandException as e2:
                    e = e2

            if "deleted in HEAD and modified in" not in e.stdout:
                raise e
        return naive_resolution_applied

    async def apply_patch_from_url_conflict_resolving(self, patch_url: str, extra_options: str = ""):
        """
        Downloads a patch file from the given URL and applies it with naive conflict resolution. If git encounters
        any modified files that don't exist in the repository, we simply include those files wholesale.
        :param patch_url: URL to the patch file
        :param extra_options: Extra arguments to be passed to ``git am``
        :return: ``True`` if naive conflict resolution was applied
        """
        r = requests.get(patch_url)
        with open(f'tmp.patch', 'wb') as f:
            f.write(r.content)
        return await self.apply_patch_conflict_resolving("../../tmp.patch", extra_options)

    async def checkout(self, branch: str):
        try:
            await self.git(f"checkout -b {branch}")
        except GitCommandException as e:
            if "already exists" not in e.stderr:
                raise e
            await self.git(f"checkout {branch}")

    async def get_remote_url(self, remote: str) -> str:
        stdout, _ = await self.git(f"remote get-url {remote}")
        return stdout.strip()

    async def hard_reset_with_remote_branch(self, target_repo_id: RepoId, branch: str):
        await self.git(f"reset --hard {target_repo_id.slug()}/{branch}")

    async def push(self, remote: Optional[str] = "", remote_branch: Optional[str] = "", force: bool = False):
        force_flag = force and "--force" or ""
        await self.git(f"push {remote} {remote_branch} {force_flag}")

    async def set_remote_url(self, remote: str, url: str):
        await self.git(f"remote set-url {remote} {url}")

    async def sync_branch_with_remote(self, remote: str, local_branch: str, remote_branch: Optional[str] = None):
        remote_branch = remote_branch or local_branch
        await self.git(f"fetch {remote}")
        await self.git(f"checkout {local_branch}")
        await self.git(f"reset --hard {remote}/{remote_branch}")

    async def track_and_fetch_remote(self, target_repo_id: RepoId):
        try:
            await self.git(f"remote add {target_repo_id.slug()} {target_repo_id.url()}")
        except GitCommandException as e:
            if "already exists." not in e.stderr:
                raise e
        await self.git(f"fetch {target_repo_id.slug()}")

    @classmethod
    async def open(cls, repo_id: RepoId, default_branch: str):
        """
        'Opens' or clones a repository in the ``./repositories`` directory.
        """
        repo_dir = f"{REPOSITORIES_DIR}/{repo_id.slug()}"
        repo = LocalRepo(repo_dir, repo_id, default_branch)

        if not os.path.exists(repo_dir):
            await repo.git(f"clone {repo_id.url()} {repo_dir}", working_directory=REPOSITORIES_DIR)

        await repo.git(f"branch -u origin/{default_branch} {default_branch}")

        return repo

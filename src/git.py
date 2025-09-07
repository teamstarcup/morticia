import asyncio
import os
from typing import Optional, Union

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


class GitCommandException(BaseException):
    stdout: str
    stderr: str
    exit_code: int

    def __init__(self, stdout: str, stderr: str, exit_code: int):
        super().__init__()
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class LocalRepo:
    default_branch: str

    def __init__(self, path: str, repo_id: RepoId, default_branch: str):
        self.path = path
        self.repo_id = repo_id
        self.default_branch = default_branch

    async def git(self, cmd: str, working_directory: Optional[Union[str, bytes, os.PathLike]] = None):
        proc = await asyncio.create_subprocess_shell(
            f"git {cmd}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_directory or self.path,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise GitCommandException(stdout.decode("cp1252"), stderr.decode("cp1252"), proc.returncode)

        return stdout, proc.returncode

    async def abort_merge(self):
        try:
            await self.git("merge --abort")
        except GitCommandException as e:
            if "There is no merge to abort" not in e.stderr:
                raise e

    async def get_remote_url(self, remote: str) -> str:
        stdout, _ = await self.git(f"remote get-url {remote}")
        return stdout.decode("cp1252").strip()

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

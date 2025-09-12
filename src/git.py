import asyncio
import os
import re
from enum import Enum
from typing import Optional, Union

import requests
from slugify import slugify

from .status import StatusMessage

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
    pass


class ResolutionType(Enum):
    UNSELECTED = -1
    MANUAL = 0
    OURS = 1
    THEIRS = 2


class MergeConflict:
    path: str
    content: str
    diff: str

    proposed_content: str
    resolution: ResolutionType

    def __init__(self, repo: LocalRepo, path: str, content: str, diff: str):
        self.repo = repo
        self.path = path
        self.content = content
        self.diff = diff

        self.proposed_content = self.content
        self.resolution = ResolutionType.UNSELECTED

    def file_path(self):
        return f"{self.repo.path}/{self.path}"

    def take_ours(self):
        self.resolution = ResolutionType.OURS

    def take_theirs(self):
        self.resolution = ResolutionType.THEIRS

    def take_manual(self):
        self.resolution = ResolutionType.MANUAL

    async def resolve(self):
        match self.resolution:
            case ResolutionType.MANUAL:
                with open(self.file_path(), "w") as f:
                    f.write(self.proposed_content)
                await self.repo.stage_file(self.path)
            case ResolutionType.OURS:
                await self.repo.git(f"checkout --ours {self.path}")
                await self.repo.stage_file(self.path)
                pass
            case ResolutionType.THEIRS:
                await self.repo.git(f"checkout --theirs {self.path}")
                await self.repo.stage_file(self.path)
                pass
            case ResolutionType.UNSELECTED:
                raise Exception("This should not happen.")


class MergeConflictsException(CommandException):
    command: str
    conflicts: list[MergeConflict]
    def __init__(self, exception: CommandException, command: str, conflicts: list[MergeConflict]):
        super().__init__(exception.stdout, exception.stderr, exception.exit_code)
        self.command = command
        self.conflicts = conflicts


# noinspection PyRedeclaration
class LocalRepo:
    path: str
    repo_id: RepoId
    default_branch: str

    status: Optional[StatusMessage]

    def __init__(self, path: str, repo_id: RepoId, default_branch: str):
        self.path = path
        self.repo_id = repo_id
        self.default_branch = default_branch

        self.status = None

    async def naive_conflict_resolution(self, e: MergeConflictsException, continue_command: str):
        naive_resolution_applied = False
        while "deleted in HEAD and modified in" in e.stdout:
            # let's get lucky
            naive_resolution_applied = True
            await self.git("add --all")
            try:
                await self.git(continue_command)
                break
            except MergeConflictsException as e2:
                e = e2

        if "deleted in HEAD and modified in" not in e.stdout:
            raise e

        return naive_resolution_applied

    async def diff(self, file_path: str):
        try:
            stdout, _ = await self.subprocess(f"difft --display=inline --color=always {file_path}")
        except CommandException as e:
            if not "Difftastic requires two paths" in e.stderr:
                raise e
            # create empty file for single-file diffing
            open(".empty.ignore", "a").close()
            stdout, _ = await self.subprocess(f"difft --display=inline --color=always ../../.empty.ignore {file_path}")
            os.remove(".empty.ignore")

        stdout = convert_discord_ansi(stdout)
        return stdout

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
        command_str = f"git {cmd}"
        if self.status:
            await self.status.write_command(command_str)

        try:
            stdout, return_code = await self.subprocess(command_str, working_directory)

            if self.status:
                await self.status.write_line(stdout)
        except CommandException as e:
            raise GitCommandException(e)
        return stdout, return_code

    async def abort_cherry_pick(self):
        try:
            await self.git("cherry-pick --abort")
        except GitCommandException as e:
            if "no cherry-pick or revert in progress" not in e.stderr:
                raise e

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
        try:
            await self.git(f"am {patch} --keep-non-patch {extra_options}")
        except GitCommandException as e:
            if not "CONFLICT" in e.stdout:
                raise e
            raise MergeConflictsException(e, "am", conflicts=await self.conflicts())

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
            naive_resolution_applied = await self.naive_conflict_resolution(e, "am --continue")
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

    async def cherry_pick(self, commit_sha: str):
        try:
            await self.git(f"cherry-pick {commit_sha}")
        except GitCommandException as e:
            if not "CONFLICT" in e.stdout:
                raise e
            raise MergeConflictsException(e, "cherry-pick", conflicts=await self.conflicts())

    async def cherry_pick_conflict_resolving(self, commit_sha: str):
        naive_resolution_applied = False
        try:
            await self.cherry_pick(commit_sha)
        except MergeConflictsException as e:
            naive_resolution_applied = await self.naive_conflict_resolution(e, "cherry-pick --continue")
        return naive_resolution_applied

    async def conflicts(self) -> list[MergeConflict]:
        stdout, exit_code = await self.git("diff --name-status --diff-filter=U")
        conflicts = []
        for line in stdout.splitlines():
            _, path = line.split("\t")
            with open(f"{self.path}/{path}", "r") as f:
                content = f.read()
            diff = await self.diff(path)
            conflicts.append(MergeConflict(self, path, content, diff))
        return conflicts

    async def continue_merge(self, command: str):
        try:
            await self.git(f"{command} --continue")
        except GitCommandException as e:
            if not "CONFLICT" in e.stdout:
                raise e
            raise MergeConflictsException(e, command, conflicts=await self.conflicts())

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

    async def stage_file(self, file_path: str):
        await self.git(f"add {file_path}")

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


ANSI_FG_COLOR_BRIGHT_LOW = 90
ANSI_FG_COLOR_BRIGHT_HIGH = 97
ANSI_FG_COLOR_BRIGHT_GREEN = 92
ANSI_FG_COLOR_BRIGHT_MAGENTA = 95
ANSI_FG_COLOR_BRIGHT_CYAN = 96
ANSI_CODE_PATTERN = re.compile(r"\x1B\[([\d;]+)m")
def convert_discord_ansi(message: str) -> str:
    """
    Strips useless ansi codes from subprocess stdout/stderr, replacing codes for Discord compatibility where possible.
    :param message:
    :return:
    """
    def closure(match: re.Match[str]):
        codes = match[1].split(";")
        new_codes = []
        for code in codes:
            code = int(code)

            # skip rendering unchanged lines in diff
            if code is ANSI_FG_COLOR_BRIGHT_MAGENTA:
                continue

            # bright FG colors to standard FG colors
            if ANSI_FG_COLOR_BRIGHT_LOW <= code <= ANSI_FG_COLOR_BRIGHT_HIGH:
                code = code is not ANSI_FG_COLOR_BRIGHT_GREEN and code or ANSI_FG_COLOR_BRIGHT_CYAN
                new_codes.append(str(code - 60))
            if code == 0:
                new_codes.append(str(code))

        if len(new_codes) <= 0:
            return ""

        return f"\x1B[{';'.join(new_codes)}m"

    return ANSI_CODE_PATTERN.sub(closure, message)

from enum import Enum
from typing import Optional

import discord
import github.File
import sqlalchemy
from github.PullRequest import PullRequest
from sqlalchemy import MetaData, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.utils import repo_id_from_url


def _unique(session, cls, hashfunc, queryfunc, constructor, arg, kw):
    cache = session.info.get("_unique_cache", None)
    if cache is None:
        session.info['_unique_cache'] = cache = {}

    key = (cls, hashfunc(*arg, **kw))
    if key in cache:
        return cache[key]
    else:
        with session.no_autoflush:
            q = session.query(cls)
            q = queryfunc(q, *arg, **kw)
            obj = q.first()
            if not obj:
                obj = constructor(*arg, **kw)
                session.add(obj)
        cache[key] = obj
        return obj


class UniqueMixin(object):
    @classmethod
    def unique_hash(cls, *arg, **kw):
        raise NotImplementedError()

    @classmethod
    def unique_filter(cls, query, *arg, **kw):
        raise NotImplementedError()

    @classmethod
    def as_unique(cls, session, *arg, **kw):
        return _unique(
                    session,
                    cls,
                    cls.unique_hash,
                    cls.unique_filter,
                    cls,
                    arg, kw
               )

    # optional asyncio version as well
    @classmethod
    async def async_as_unique(cls, async_session, *arg, **kw):
        return await async_session.run_sync(cls.as_unique, *arg, **kw)


class FileChangeStatus(Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    RENAMED = "renamed"

    COPIED = "copied"
    CHANGED = "changed"
    UNCHANGED = "unchanged"

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s"
    })
    pass

class KnownRepo(Base, UniqueMixin):
    __tablename__ = "known_repos"

    repo_id: Mapped[str] = mapped_column(primary_key=True)

    @classmethod
    def unique_hash(cls, repo_id):
        return repo_id

    @classmethod
    def unique_filter(cls, query, repo_id):
        return query.filter(KnownRepo.repo_id == repo_id)


class KnownFile(Base, UniqueMixin):
    __tablename__ = "known_files"

    repo_id: Mapped[str] = mapped_column(ForeignKey("known_repos.repo_id"), primary_key=True)
    file_path: Mapped[str] = mapped_column(primary_key=True)

    @classmethod
    def unique_hash(cls, repo_id, file_path):
        return f"{repo_id}@{file_path}"

    @classmethod
    def unique_filter(cls, query, repo_id, file_path):
        return query.filter(KnownFile.repo_id == repo_id and KnownFile.file_path == file_path)


class KnownPullRequest(Base, UniqueMixin):
    __tablename__ = "known_pull_requests"

    pull_request_id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("known_repos.repo_id"), primary_key=True)

    title: Mapped[str]
    body: Mapped[str]

    state: Mapped[str]
    merged: Mapped[bool]

    additions: Mapped[int]
    deletions: Mapped[int]

    changed_files: Mapped[int]
    commits: Mapped[int]
    comments: Mapped[int]

    ref_base: Mapped[str]
    ref_head: Mapped[str]

    created_at: Mapped[int]
    updated_at: Mapped[Optional[int]]
    closed_at: Mapped[Optional[int]]
    merged_at: Mapped[Optional[int]]

    def update(self, pull_request: PullRequest):
        # self.pull_request_id = pull_request.number
        # self.repo_id = repo_id_from_url(pull_request.html_url)
        self.title = pull_request.title
        self.body = pull_request.body
        self.state = pull_request.state
        self.merged = pull_request.merged
        self.additions = pull_request.additions
        self.deletions = pull_request.deletions
        self.changed_files = pull_request.changed_files
        self.commits = pull_request.commits
        self.comments = pull_request.comments
        self.ref_base = pull_request.base.ref
        self.ref_head = pull_request.head.ref
        self.created_at = int(pull_request.created_at.timestamp())
        self.updated_at = int(pull_request.updated_at.timestamp())
        if pull_request.closed_at:
            self.closed_at = int(pull_request.closed_at.timestamp())
        if pull_request.merged_at:
            self.merged_at = int(pull_request.merged_at.timestamp())

    @classmethod
    def unique_hash(cls, pull_request_id, repo_id):
        return f"{repo_id}#{pull_request_id}"

    @classmethod
    def unique_filter(cls, query, pull_request_id, repo_id):
        return query.filter(KnownPullRequest.pull_request_id == pull_request_id and KnownPullRequest.repo_id == repo_id)


class KnownFileChange(Base, UniqueMixin):
    __tablename__ = "known_file_changes"

    pull_request_id: Mapped[str] = mapped_column(ForeignKey("known_pull_requests.pull_request_id"), primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("known_repos.repo_id"), primary_key=True)
    file_path: Mapped[str] = mapped_column(ForeignKey("known_files.file_path"), primary_key=True)

    status: Mapped[str]

    additions: Mapped[int]
    changes: Mapped[int]
    deletions: Mapped[int]

    previous_file_path: Mapped[Optional[str]]
    patch: Mapped[Optional[str]]
    sha: Mapped[str]

    def update(self, file: github.File.File,):
        self.file_path = file.filename
        self.status = file.status
        self.additions = file.additions
        self.changes = file.changes
        self.deletions = file.deletions
        self.previous_file_path = file.previous_filename
        self.patch = file.patch
        self.sha = file.sha

    @classmethod
    def unique_hash(cls, pull_request_id, repo_id, file_path):
        return f"{repo_id}#{pull_request_id}:{file_path}"

    @classmethod
    def unique_filter(cls, query, pull_request_id, repo_id, file_path):
        return query.filter(
            KnownFileChange.pull_request_id == pull_request_id and
            KnownFileChange.repo_id == repo_id and
            KnownFileChange.file_path == file_path
        )

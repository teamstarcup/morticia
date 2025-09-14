from datetime import datetime
from enum import Enum
from typing import Optional

import github.File
from github.PullRequest import PullRequest
from sqlalchemy import MetaData, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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

    # noinspection PyMethodOverriding
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

    # noinspection PyMethodOverriding
    @classmethod
    def unique_filter(cls, query, repo_id, file_path):
        return query.filter(KnownFile.repo_id == repo_id).filter(KnownFile.file_path == file_path)


class KnownPullRequest(Base, UniqueMixin):
    __tablename__ = "known_pull_requests"

    pull_request_id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("known_repos.repo_id"), primary_key=True)

    title: Mapped[str]
    body: Mapped[Optional[str]]

    state: Mapped[str]
    merged: Mapped[bool]

    additions: Mapped[int]
    deletions: Mapped[int]

    changed_files: Mapped[int]
    commits: Mapped[int]
    comments: Mapped[int]

    ref_base: Mapped[str]
    ref_head: Mapped[str]

    created_at: Mapped[datetime]
    updated_at: Mapped[Optional[datetime]]
    closed_at: Mapped[Optional[datetime]]
    merged_at: Mapped[Optional[datetime]]

    def update(self, pull_request: PullRequest):
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
        self.created_at = pull_request.created_at
        self.updated_at = pull_request.updated_at
        if pull_request.closed_at:
            self.closed_at = pull_request.closed_at
        if pull_request.merged_at:
            self.merged_at = pull_request.merged_at

    @property
    def html_url(self):
        return f"https://github.com/{self.repo_id}/pull/{self.pull_request_id}"

    @classmethod
    def unique_hash(cls, pull_request_id, repo_id):
        return f"{repo_id}#{pull_request_id}"

    # noinspection PyMethodOverriding
    @classmethod
    def unique_filter(cls, query, pull_request_id, repo_id):
        return query.filter(KnownPullRequest.pull_request_id == pull_request_id).filter(KnownPullRequest.repo_id == repo_id)


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
    sha: Mapped[Optional[str]]

    pull_request: Mapped[KnownPullRequest] = relationship()

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

    # noinspection PyMethodOverriding
    @classmethod
    def unique_filter(cls, query, pull_request_id, repo_id, file_path):
        return query.filter(KnownFileChange.pull_request_id == pull_request_id).filter(KnownFileChange.repo_id == repo_id).filter(KnownFileChange.file_path == file_path)

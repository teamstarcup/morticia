from enum import Enum
from typing import Optional

from sqlalchemy import MetaData, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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

class KnownRepo(Base):
    __tablename__ = "known_repos"

    repo_id: Mapped[str] = mapped_column(primary_key=True)

class KnownFile(Base):
    __tablename__ = "known_files"

    repo_id: Mapped[KnownRepo] = mapped_column(ForeignKey("known_repos.repo_id"), primary_key=True)
    file_path: Mapped[str] = mapped_column(primary_key=True)

class KnownPullRequest(Base):
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

class KnownFileChange(Base):
    __tablename__ = "known_file_changes"

    pull_request_id: Mapped[str] = mapped_column(ForeignKey("known_pull_requests.pull_request_id"), primary_key=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("known_repos.repo_id"), primary_key=True)
    file_path: Mapped[str] = mapped_column(ForeignKey("known_files.file_path"), primary_key=True)

    status: Mapped[str]

    additions: Mapped[int]
    changes: Mapped[int]
    deletions: Mapped[int]

    previous_file_path: Mapped[Optional[str]]
    patch: Mapped[str]
    sha: Mapped[str]

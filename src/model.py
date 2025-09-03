from sqlalchemy import String, MetaData, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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

# SPDX-FileCopyrightText: 2020–2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterable, Iterator, Optional, Protocol, TypeVar, Union

from git import BaseGit


class ImportTransactionProtocol(Protocol):

    def begin(self, import_branch: str) -> None: ...

    def commit(self, commit_message: Optional[str] = None) -> None: ...

    def rollback(self) -> None: ...

    def add_file(self, file: Union[Path, str]) -> None: ...

    def add_files(self, files: Iterable[Union[Path, str]]) -> None: ...

    def move_file_to_annex(self,
                           source: Union[Path, str],
                           dest: Union[Path, str]) -> None: ...

    def set_commit_message(self, commit_message: str) -> None: ...


class ImportTransaction:

    def __init__(self, git: BaseGit):
        self.git = git

    def begin(self, import_branch: str) -> None:
        assert self.git.working_directory_is_clean()
        self.old_branch = self.git.current_branch()
        self.git.change_branch(import_branch)
        self.files_to_move_to_annex: list[tuple[Path, Path]] = []
        self.files_to_add_to_git: list[Path] = []

    def commit(self, commit_message: Optional[str] = None) -> None:
        if commit_message is None:
            commit_message = self.commit_message
        self.git.add_files(self.files_to_add_to_git)
        files_to_annex = []
        for from_, to in self.files_to_move_to_annex:
            os.rename(from_, to)
            files_to_annex.append(to)
        self.git.add_files_to_annex(files_to_annex)
        self.git.commit(commit_message)
        self.git.change_branch(self.old_branch)
        del self.old_branch

    def rollback(self) -> None:
        del self.files_to_move_to_annex
        # add files to git so that they are properly reset --hard
        self.git.add_files(self.files_to_add_to_git)
        del self.files_to_add_to_git
        self.git.reset_index_and_working_directory()
        self.git.change_branch(self.old_branch)
        del self.old_branch

    def add_file(self, file: Union[Path, str]) -> None:
        self.files_to_add_to_git.append(Path(file))

    def add_files(self, files: Iterable[Union[Path, str]]) -> None:
        self.files_to_add_to_git.extend(Path(file) for file in files)

    def move_file_to_annex(self,
                           source: Union[Path, str],
                           dest: Union[Path, str]) -> None:
        self.files_to_move_to_annex.append((Path(source), Path(dest)))

    def set_commit_message(self, commit_message: str) -> None:
        self.commit_message = commit_message


class FakeImportTransaction:

    def __init__(self, _git: BaseGit):
        pass

    def begin(self, import_branch: str) -> None:
        print("beginning fake import Git transaction on branch "
              + import_branch)
        self.files_to_move_to_annex: list[tuple[Path, Path]] = []
        self.files_to_add_to_git: list[Path] = []

    def commit(self, commit_message: Optional[str] = None) -> None:
        if commit_message is None:
            commit_message = self.commit_message
        print("fake Git commit with message:\n"
              + commit_message)

    def rollback(self) -> None:
        print("rolling back fake Git import transaction")

    def add_file(self, file: Union[Path, str]) -> None:
        print(f"adding file {file} to import transaction")

    def add_files(self, files: Iterable[Union[Path, str]]) -> None:
        print(f"adding files {', '.join(str(f) for f in files)} "
              "to import transaction")

    def move_file_to_annex(self,
                           source: Union[Path, str],
                           dest: Union[Path, str]) -> None:
        self.files_to_move_to_annex.append((Path(source), Path(dest)))
        print(f"moving {source} to {dest}")

    def set_commit_message(self, commit_message: str) -> None:
        self.commit_message = commit_message


@contextmanager
def import_transaction(
        git: BaseGit,
        import_branch: str,
        dry_run: bool) -> Iterator[ImportTransactionProtocol]:
    transaction: ImportTransactionProtocol
    if dry_run:
        transaction = FakeImportTransaction(git)
    else:
        transaction = ImportTransaction(git)
    transaction.begin(import_branch)
    try:
        yield transaction
    except Exception as e:
        transaction.rollback()
        raise e
    else:
        if hasattr(transaction, 'commit_message'):
            transaction.commit()
        else:
            transaction.rollback()

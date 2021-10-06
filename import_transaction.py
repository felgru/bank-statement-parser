# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
import os
from typing import Iterable, Optional, TypeVar, Union

from git import BaseGit


class ImportTransaction:

    def __init__(self, git: BaseGit):
        self.git = git

    def begin(self, import_branch: str) -> None:
        assert self.git.working_directory_is_clean()
        self.old_branch = self.git.current_branch()
        self.git.change_branch(import_branch)
        self.files_to_move_to_annex: list[tuple[str, str]] = []
        self.files_to_add_to_git: list[str] = []

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

    def add_file(self, file: str) -> None:
        self.files_to_add_to_git.append(file)

    def add_files(self, files: Iterable[str]) -> None:
        self.files_to_add_to_git.extend(files)

    def move_file_to_annex(self, source: str, dest: str) -> None:
        self.files_to_move_to_annex.append((source, dest))

    def set_commit_message(self, commit_message: str) -> None:
        self.commit_message = commit_message


class FakeImportTransaction:

    def __init__(self, _git: BaseGit):
        pass

    def begin(self, import_branch: str) -> None:
        print("beginning fake import Git transaction on branch "
              + import_branch)
        self.files_to_move_to_annex: list[tuple[str, str]] = []
        self.files_to_add_to_git: list[str] = []

    def commit(self, commit_message: Optional[str] = None) -> None:
        if commit_message is None:
            commit_message = self.commit_message
        print("fake Git commit with message:\n"
              + commit_message)

    def rollback(self) -> None:
        print("rolling back fake Git import transaction")

    def add_file(self, file: str) -> None:
        print(f"adding file {file} to import transaction")

    def add_files(self, files: Iterable[str]) -> None:
        print(f"adding files {', '.join(files)} to import transaction")

    def move_file_to_annex(self, source: str, dest: str) -> None:
        self.files_to_move_to_annex.append((source, dest))
        print(f"moving {source} to {dest}")

    def set_commit_message(self, commit_message: str) -> None:
        self.commit_message = commit_message


@contextmanager
def import_transaction(git: BaseGit, import_branch: str, dry_run: bool):
    transaction: Union[FakeImportTransaction, ImportTransaction]
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

ImportTransactionProtocol = TypeVar('ImportTransactionProtocol',
                                    ImportTransaction,
                                    FakeImportTransaction)

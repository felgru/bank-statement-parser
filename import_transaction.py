# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
import os


class ImportTransaction:

    def __init__(self, git):
        self.git = git

    def begin(self, import_branch):
        assert self.git.working_directory_is_clean()
        self.old_branch = self.git.current_branch()
        self.git.change_branch(import_branch)
        self.files_to_move_to_annex = []
        self.files_to_add_to_git = []

    def commit(self, commit_message=None):
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

    def rollback(self):
        del self.files_to_move_to_annex
        # add files to git so that they are properly reset --hard
        self.git.add_files(self.files_to_add_to_git)
        del self.files_to_add_to_git
        self.git.reset_index_and_working_directory()
        self.git.change_branch(self.old_branch)
        del self.old_branch

    def add_file(self, file):
        self.files_to_add_to_git.append(file)

    def move_file_to_annex(self, source, dest):
        self.files_to_move_to_annex.append((source, dest))

    def set_commit_message(self, commit_message):
        self.commit_message = commit_message


@contextmanager
def import_transaction(git, import_branch):
    transaction = ImportTransaction(git)
    transaction.begin(import_branch)
    try:
        yield transaction
    except:
        transaction.rollback()
    else:
        if hasattr(transaction, 'commit_message'):
            transaction.commit()
        else:
            transaction.rollback()

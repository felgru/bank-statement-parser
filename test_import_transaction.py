# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pytest

from git import Git
from import_transaction import import_transaction


def create_first_commit(working_dir: Path) -> Git:
    # import_transaction needs the import branch to pre-exist.
    # Therefore it does not work without any commit.
    git = Git.create(working_dir)
    my_file = working_dir / 'initial_file'
    my_file.touch()
    git.add_file(my_file)
    git.commit('first commit')
    return git


def test_import_transaction(tmp_path: Path) -> None:
    git = create_first_commit(tmp_path)
    test_files = [tmp_path / f'test{i}' for i in range(3)]
    with import_transaction(git, 'master', dry_run=False) as transaction:
        for f in test_files:
            f.touch()
        transaction.add_file(test_files[0])
        transaction.add_files(test_files[1:])
        transaction.set_commit_message('test commit')
    assert git.working_directory_is_clean()
    for f in test_files:
        assert f.exists()


def test_rollback_on_missing_commit_message(tmp_path: Path) -> None:
    git = create_first_commit(tmp_path)
    test_files = [tmp_path / f'test{i}' for i in range(3)]
    with import_transaction(git, 'master', dry_run=False) as transaction:
        for f in test_files:
            f.touch()
        transaction.add_file(test_files[0])
        transaction.add_files(test_files[1:])
        # Don't set a commit message to force a rollback.
    assert git.working_directory_is_clean()
    for f in test_files:
        assert not f.exists()


def test_rollback_on_exception(tmp_path: Path) -> None:
    git = create_first_commit(tmp_path)
    test_files = [tmp_path / f'test{i}' for i in range(3)]
    with pytest.raises(RuntimeError):
        with import_transaction(git, 'master', dry_run=False) as transaction:
            for f in test_files:
                f.touch()
            transaction.add_file(test_files[0])
            transaction.add_files(test_files[1:])
            transaction.set_commit_message('test commit')
            raise RuntimeError('Some random exception')
    assert git.working_directory_is_clean()
    for f in test_files:
        assert not f.exists()

# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pytest

from git import Git, GitError, GitMergeConflictError


@pytest.fixture
def git_fixture(tmp_path) -> Git:
    return Git.create(tmp_path)


def test_create_git_dir(tmp_path):
    Git.create(tmp_path)
    assert (tmp_path / '.git').exists()


def test_create_commit(tmp_path: Path) -> None:
    git = Git.create(tmp_path)
    my_file = tmp_path / 'a'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add a')


def test_checkout_current_branch(tmp_path: Path) -> None:
    # The master branch only gets created once we create a first commit.
    git = Git.create(tmp_path)
    my_file = tmp_path / 'a'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add a')

    current_branch = git.current_branch()
    git.change_branch(current_branch)
    assert git.current_branch() == current_branch


def test_create_branch(tmp_path: Path) -> None:
    # Create_branch does not work in an empty repository.
    # Hence, I create a commit on the master branch.
    git = Git.create(tmp_path)
    my_file = tmp_path / 'a'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add a')

    # Now create the new branch
    original_branch = git.current_branch()
    new_branch = 'my-new-branch'
    git.create_branch(new_branch)
    assert git.current_branch() == original_branch


def test_checkout_new_branch(tmp_path: Path) -> None:
    # Create_branch does not work in an empty repository.
    # Hence, I create a commit on the master branch.
    git = Git.create(tmp_path)
    my_file = tmp_path / 'a'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add a')

    new_branch = 'my-new-branch'
    git.create_branch(new_branch, switch=True)
    assert git.current_branch() == new_branch


def test_checkout_nonexistent_branch(git_fixture: Git) -> None:
    git = git_fixture
    with pytest.raises(GitError, match='switch to non-existent branch'):
        git.change_branch('nonexistent')


def test_merge(tmp_path: Path) -> None:
    git = Git.create(tmp_path)
    my_file = tmp_path / 'a'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add a')

    main_branch = git.current_branch()
    git.create_branch('b', switch=True)
    my_file = tmp_path / 'b'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add b')

    git.change_branch(main_branch)
    my_file = tmp_path / 'c'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add c')

    git.merge('b')

    assert (tmp_path / 'a').exists()
    assert (tmp_path / 'b').exists()
    assert (tmp_path / 'c').exists()


def test_merge_conflict(tmp_path: Path) -> None:
    git = Git.create(tmp_path)
    my_file = tmp_path / 'a'
    my_file.touch()
    git.add_file(my_file)
    git.commit('Add a')

    main_branch = git.current_branch()
    git.create_branch('bad', switch=True)
    my_file = tmp_path / 'a'
    with my_file.open('w') as f:
        f.write('1')
    git.add_file(my_file)
    my_file = tmp_path / 'b'
    with my_file.open('w') as f:
        f.write('1')
    git.add_file(my_file)
    git.commit('Add b')

    git.change_branch(main_branch)
    my_file = tmp_path / 'a'
    with my_file.open('w') as f:
        f.write('2')
    git.add_file(my_file)
    my_file = tmp_path / 'b'
    with my_file.open('w') as f:
        f.write('2')
    git.add_file(my_file)
    git.commit('Add b')

    with pytest.raises(GitMergeConflictError) as exc_info:
        git.merge('bad')
    conflicts = exc_info.value.conflicts
    assert conflicts[0].name == 'b'
    assert conflicts[0].type == 'add/add'
    assert conflicts[1].name == 'a'
    assert conflicts[1].type == 'content'

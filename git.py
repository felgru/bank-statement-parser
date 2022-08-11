# SPDX-FileCopyrightText: 2020–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from abc import ABCMeta, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Optional, Union


class BaseGit(metaclass=ABCMeta):

    @abstractmethod
    def working_directory_is_clean(self) -> bool: pass

    @abstractmethod
    def has_staged_files(self) -> bool: pass

    @abstractmethod
    def current_branch(self) -> str: pass

    @abstractmethod
    def change_branch(self, branch: str) -> None: pass

    @abstractmethod
    def add_file(self, file: Union[Path, str]) -> None: pass

    @abstractmethod
    def add_files(self, files: Iterable[Union[Path, str]]) -> None: pass

    @abstractmethod
    def add_files_to_annex(self, files: Sequence[Union[Path, str]]) -> None:
        pass

    @abstractmethod
    def commit(self, message: str) -> None: pass

    @abstractmethod
    def merge(self, other_branch: str) -> None: pass

    @abstractmethod
    def reset_index_and_working_directory(self) -> None: pass


class Git(BaseGit):

    def __init__(self,
                 work_tree: Union[Path, str],
                 git_dir: Union[Path, str]):
        self.git_command = ['git', f'--work-tree={work_tree}',
                                   f'--git-dir={git_dir}']
        self._check_for_annex(git_dir)

    @classmethod
    def create(cls, work_tree: Union[Path, str]) -> Git:
        """Create a new git repository."""
        work_tree = Path(work_tree)
        git_dir = work_tree / '.git'
        git = cls(work_tree=work_tree, git_dir=git_dir)
        git._run_git_command(['init', str(work_tree)])
        git._check_for_annex(git_dir)
        return git

    def has_annex(self) -> bool:
        return self._has_annex

    def _check_for_annex(self, git_dir: Union[Path, str]) -> None:
        # was git annex initialized in the Git repository?
        self._has_annex = Path(git_dir, 'annex').exists()

    def _run_git_command(self,
                         args: list[str],
                         input: Optional[str] = None) -> str:
        return subprocess.run(self.git_command + args,
                              capture_output=True, encoding='UTF8',
                              input=input, check=True).stdout

    def working_directory_is_clean(self) -> bool:
        return not self._has_files_with_any_of_these_status_flags('MADRCU')

    def has_staged_files(self) -> bool:
        return self._has_files_with_any_of_these_status_flags('ADRC')

    def _has_files_with_any_of_these_status_flags(self,
                                                  flags: Iterable[str]) -> bool:
        status = self._run_git_command(['status', '--porcelain'])
        change_flags = set(flags)
        for line in status.split('\n')[:-1]:
            stat = line.split()[0]
            if not change_flags.isdisjoint(stat):
                return True
        return False

    def current_branch(self) -> str:
        return self._run_git_command(['branch', '--show-current']).strip()

    def change_branch(self, branch: str) -> None:
        try:
            self._run_git_command(['switch', branch])
        except subprocess.CalledProcessError as e:
            m = re.match(r'fatal: invalid reference: (.*)\n$', e.stderr)
            if m is None:
                raise e
            assert m.group(1) == branch
            raise GitError(
                    f'Trying to switch to non-existent branch: {branch}.'
                    ) from None

    def create_branch(self,
                      branch: str,
                      start_point: Optional[str] = None,
                      *,
                      switch: bool = False,
                      ) -> None:
        start = [] if start_point is None else [start_point]
        if not switch:
            self._run_git_command(['branch', branch, *start])
        else:
            self._run_git_command(['switch', '-c', branch, *start])

    def add_file(self, file: Union[Path, str]) -> None:
        self._run_git_command(['add', str(file)])

    def add_files(self, files: Iterable[Union[Path, str]]) -> None:
        self._run_git_command(['add', *(str(file) for file in files)])

    def add_files_to_annex(self, files: Sequence[Union[Path, str]]) -> None:
        if not (self.has_annex() and len(files) > 0):
            return
        self._run_git_command(['annex', 'add', '--force-large',
                               *(str(file) for file in files)])

    def commit(self, message: str) -> None:
        self._run_git_command(['commit', '--file=-'], input=message)

    def merge(self, other_branch: str, message: Optional[str] = None) -> None:
        try:
            if message is None:
                self._run_git_command(['merge', '--no-edit', other_branch])
            else:
                self._run_git_command(['merge', '--file=-', other_branch],
                                      input=message)
        except subprocess.CalledProcessError as e:
            assert 'CONFLICT' in e.stdout
            raise GitMergeConflictError.from_stdout(e.stdout) from None

    def reset_index_and_working_directory(self) -> None:
        self._run_git_command(['reset', '--hard', 'HEAD'])


class GitError(Exception):
    pass


@dataclass
class Conflict:
    name: str
    type: str


class GitMergeConflictError(GitError):
    def __init__(self, conflicts: list[Conflict]):
        self.conflicts = conflicts

    @classmethod
    def from_stdout(cls, stdout: str) -> GitMergeConflictError:
        ms = re.finditer(r'CONFLICT \(([^\)]*)\): '
                         r'Merge conflict in ([^\n]*)\n',
                         stdout)
        return cls([Conflict(name=m.group(2),
                             type=m.group(1))
                    for m in ms])

    def __str__(self) -> str:
        return ('Merge conflict in the following files:\n'
                + '\n'.join(f'({c.type}) {c.name}' for c in self.conflicts))


class FakeGit(BaseGit):

    def __init__(self) -> None:
        pass

    def working_directory_is_clean(self) -> bool:
        return True

    def has_staged_files(self) -> bool:
        return False

    def current_branch(self) -> str:
        return 'not using Git'

    def change_branch(self, branch: str) -> None:
        pass

    def add_file(self, file: Union[Path, str]) -> None:
        pass

    def add_files(self, files: Iterable[Union[Path, str]]) -> None:
        pass

    def add_files_to_annex(self, files: Sequence[Union[Path, str]]) -> None:
        pass

    def commit(self, message: str) -> None:
        pass

    def merge(self, other_branch: str) -> None:
        pass

    def reset_index_and_working_directory(self) -> None:
        pass

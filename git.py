# SPDX-FileCopyrightText: 2020–2021 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from abc import ABCMeta, abstractmethod
import os
from pathlib import Path
import subprocess
from typing import Iterable, Optional, Sequence, Union


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
    def reset_index_and_working_directory(self) -> None: pass


class Git(BaseGit):

    def __init__(self,
                 work_tree: Union[Path, str],
                 git_dir: Union[Path, str]):
        self.git_command = ['git', f'--work-tree={work_tree}',
                                   f'--git-dir={git_dir}']
        # was git annex initialized in the Git repository?
        self._has_annex = Path(git_dir, 'annex').exists()

    def has_annex(self) -> bool:
        return self._has_annex

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
        self._run_git_command(['checkout', branch])

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

    def reset_index_and_working_directory(self) -> None:
        self._run_git_command(['reset', '--hard', 'HEAD'])


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

    def reset_index_and_working_directory(self) -> None:
        pass

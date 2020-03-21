# SPDX-FileCopyrightText: 2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess


class Git:

    def __init__(self, git_dir):
        self.git_command = ['git', f'--git-dir={git_dir}']
        # was git annex initialized in the Git repository?
        self._has_annex = os.path.exists(os.path.join(git_dir, 'annex'))

    def has_annex(self):
        return self._has_annex

    def _run_git_command(self, args, input=None):
        return subprocess.run(self.git_command + args,
                              capture_output=True, encoding='UTF8',
                              input=input, check=True).stdout

    def working_directory_is_clean(self):
        return not self._has_files_with_any_of_these_status_flags('MADRCU')

    def has_staged_files(self):
        return self._has_files_with_any_of_these_status_flags('ADRC')

    def _has_files_with_any_of_these_status_flags(self, flags):
        status = self._run_git_command(['status', '--porcelain'])
        change_flags = set(flags)
        for line in status.split('\n')[:-1]:
            stat = line.split()[0]
            if not change_flags.isdisjoint(stat):
                return True
        return False

    def current_branch(self):
        return self._run_git_command(['branch', '--show-current']).strip()

    def change_branch(self, branch):
        self._run_git_command(['checkout', branch])

    def add_file(self, file):
        self._run_git_command(['add', file])

    def add_files(self, files):
        self._run_git_command(['add'] + files)

    def add_files_to_annex(self, files):
        if not (self.has_annex() and files):
            return
        self._run_git_command(['annex', 'add', '--force-large'] + files)

    def commit(self, message):
        self._run_git_command(['commit', '--file=-'], input=message)

    def reset_index_and_working_directory(self):
        self._run_git_command(['reset', '--hard', 'HEAD'])


class FakeGit:

    def __init__(self, git_dir):
        pass

    def working_directory_is_clean(self):
        return True

    def has_staged_files(self):
        return False

    def current_branch(self):
        return 'not using Git'

    def change_branch(self, branch):
        pass

    def add_file(self, file):
        pass

    def add_files(self, files):
        pass

    def add_files_to_annex(self, files):
        pass

    def commit(self, message):
        pass

    def reset_index_and_working_directory(self):
        pass

# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path
from typing import Optional

import pytest

from git import FakeGit
from include_files import write_include_files


def create_test_dir(base_dir: Path,
                    test_files: list) -> None:
    for t in test_files:
        if isinstance(t, str):
            (base_dir / t).touch()
        elif isinstance(t, tuple):
            subdir = base_dir / t[0]
            subdir.mkdir()
            create_test_dir(subdir, t[1])
        else:
            raise ValueError(f'Unexpected test data: {t}.')


def check_generated_files(base_dir: Path,
                          test_files: list) -> None:
    check_dir_equal_to(base_dir, test_files)
    check_include_content(base_dir)


def check_dir_equal_to(base_dir: Path,
                       test_files: list) -> None:
    assert base_dir.is_dir()
    dir_content = {p.name for p in base_dir.iterdir()}
    expected_dir_content = set()
    for t in test_files:
        if isinstance(t, str):
            expected_dir_content.add(t)
        elif isinstance(t, tuple):
            subdir = base_dir / t[0]
            check_dir_equal_to(subdir, t[1])
            expected_dir_content.add(t[0])
    assert expected_dir_content == dir_content


def check_include_content(base_dir: Path) -> bool:
    assert base_dir.is_dir()
    journal_file: Optional[Path] = None
    directories: list[str] = []
    files: list[str] = []
    for p in base_dir.iterdir():
        if p.name == 'journal.hledger':
            journal_file = p
        elif p.is_dir():
            if check_include_content(p):
                directories.append(
                        str(p.relative_to(base_dir) / 'journal.hledger'))
        elif p.suffix == '.hledger':
            files.append(p.name)
    if journal_file is None:
        return False
    directories.sort()
    files.sort()
    includes = []
    with journal_file.open() as f:
        for line in f:
            if line.startswith('include '):
                includes.append(line[len('include '):].rstrip())
    assert directories + files == includes
    return True


def test_write_include_files_for_the_first_time(tmp_path):
    create_test_dir(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['test.hledger']),
                ('bank_b', ['test.hledger', 'test2.hledger']),
             ]),
            ('bank_a', ['q4.hledger']),
          ]),
         ('2022', [
            ('1', [
                ('bank_a', ['test.hledger']),
                ('bank_b', ['test.hledger']),
             ]),
          ]),
         'some_other.hledger',
         ]
    )
    git = FakeGit()
    write_include_files(tmp_path, git)
    check_generated_files(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                ('bank_b', ['journal.hledger', 'test.hledger', 'test2.hledger']),
                'journal.hledger',
             ]),
            ('bank_a', ['journal.hledger', 'q4.hledger']),
            'journal.hledger',
          ]),
         ('2022', [
            ('1', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                ('bank_b', ['journal.hledger', 'test.hledger']),
                'journal.hledger',
             ]),
            'journal.hledger',
          ]),
         'journal.hledger',
         'some_other.hledger',
         ]
    )


def test_write_include_files_with_existing_include_files(tmp_path):
    create_test_dir(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                ('bank_b', ['journal.hledger', 'test.hledger', 'test2.hledger']),
                'journal.hledger',
             ]),
            ('bank_a', ['journal.hledger', 'q4.hledger']),
            'journal.hledger',
          ]),
         ('2022', [
            ('1', [
                ('bank_a', ['test.hledger']),
                ('bank_b', ['test.hledger']),
             ]),
          ]),
         'some_other.hledger',
         ]
    )
    git = FakeGit()
    write_include_files(tmp_path, git)
    check_generated_files(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                ('bank_b', ['journal.hledger', 'test.hledger', 'test2.hledger']),
                'journal.hledger',
             ]),
            ('bank_a', ['journal.hledger', 'q4.hledger']),
            'journal.hledger',
          ]),
         ('2022', [
            ('1', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                ('bank_b', ['journal.hledger', 'test.hledger']),
                'journal.hledger',
             ]),
            'journal.hledger',
          ]),
         'journal.hledger',
         'some_other.hledger',
         ]
    )


def test_leaf_dir_without_ledger_should_not_contain_include_file(tmp_path):
    create_test_dir(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                ('bank_b', ['not_a_journal']),
                'journal.hledger',
             ]),
            ('bank_a', ['journal.hledger', 'q4.hledger']),
            'journal.hledger',
          ]),
         ('2022', [
            ('1', [
                ('bank_a', []),
             ]),
          ]),
         'journal.hledger',
         ]
    )
    git = FakeGit()
    write_include_files(tmp_path, git)
    check_generated_files(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                ('bank_b', ['not_a_journal']),
                'journal.hledger',
             ]),
            ('bank_a', ['journal.hledger', 'q4.hledger']),
            'journal.hledger',
          ]),
         ('2022', [
            ('1', [
                ('bank_a', []),
             ]),
          ]),
         'journal.hledger',
         ]
    )


def test_unnecessary_include_files_should_be_removed(tmp_path):
    create_test_dir(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                'journal.hledger',
             ]),
            'journal.hledger',
          ]),
         ('2022', [
            ('1', [
                ('bank_a', ['journal.hledger', 'not_a_journal']),
                'journal.hledger',
             ]),
          ]),
         'journal.hledger',
         ]
    )
    git = FakeGit()
    write_include_files(tmp_path, git)
    check_generated_files(
        tmp_path,
        [('2021', [
            ('12', [
                ('bank_a', ['journal.hledger', 'test.hledger']),
                'journal.hledger',
             ]),
            'journal.hledger',
          ]),
         ('2022', [
            ('1', [
                ('bank_a', ['not_a_journal']),
             ]),
          ]),
         'journal.hledger',
         ]
    )

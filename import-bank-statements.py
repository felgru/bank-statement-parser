#!/usr/bin/python3

# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
from collections.abc import Mapping
import configparser
from datetime import date, timedelta
import io
import os
from pathlib import Path
import sys
from typing import Iterable, Protocol, Union

from git import BaseGit, FakeGit, Git
from import_transaction import (
        DirtyWorkingDirectoryException,
        import_transaction,
        ImportTransactionProtocol,
        )
from parsers.banks import parsers
from parsers.parser import Parser
from xdg_dirs import getXDGdirectories


def import_incoming_statements(dirs: Mapping[str, str],
                               git: BaseGit,
                               import_branch: str,
                               force: bool,
                               dry_run: bool) -> None:
    with import_transaction(git, import_branch, dry_run) as transaction:
        incoming_dir = dirs['incoming']
        import_summary = dict()
        for (dirpath, dirnames, filenames) in os.walk(incoming_dir):
            if dirpath == incoming_dir:
                continue
            bank = os.path.basename(dirpath)
            if bank not in parsers:
                print('unknown bank:', bank, file=sys.stderr)
                continue
            bank_parsers = parsers[bank]
            if filenames:
                print('importing bank statements from', bank)
            dateranges = []
            imported_files = []
            filenames.sort()
            for f in filenames:
                try:
                    extension = os.path.splitext(f)[1].lower()
                    Parser = bank_parsers[extension]
                except KeyError:
                    continue
                src_file = Path(dirpath, f)
                parser = Parser(src_file)
                m = parser.parse_metadata()
                print(f'{m.start_date} → {m.end_date}: {src_file}')
                mid_date = m.start_date + (m.end_date - m.start_date) / 2
                year = str(mid_date.year)
                if m.end_date - m.start_date <= timedelta(weeks=6):
                    month = str(mid_date.month).zfill(2)
                    dest_dir = os.path.join(dirs['ledgers'], year, month, bank)
                else:
                    dest_dir = os.path.join(dirs['ledgers'], year, bank)
                os.makedirs(dest_dir, exist_ok=True)
                dest_file = Path(dest_dir, f).with_suffix('.hledger')
                if parse_and_write_bank_statement(parser, src_file, dest_file,
                                                  transaction, force, dry_run):
                    imported_files.append((f, m.start_date, m.end_date))
                    dateranges.append((m.start_date, m.end_date))
            merge_dateranges(dateranges)
            if dateranges:
                dateranges_disp = ', '.join('{} → {}'.format(*d)
                                            for d in dateranges)
                print(f'imported {bank} bank statements for {dateranges_disp}')
                summary = f'{bank}:\n{dateranges_disp}\n\n' \
                          + '\n'.join('* {1} → {2}: {0}'.format(*im)
                                      for im in imported_files)
                import_summary[bank] = summary
        if not dry_run:
            write_include_files(config['dirs']['ledgers'], transaction)
        if import_summary:
            commit_message = 'import bank statements\n\n'
            commit_message += '\n\n'.join(s for _, s in sorted(import_summary
                                                               .items()))
            transaction.set_commit_message(commit_message)

def parse_and_write_bank_statement(
        parser: Parser,
        src_file: Path,
        dest_file: Path,
        import_transaction: ImportTransactionProtocol,
        force: bool,
        dry_run: bool) -> bool:
    if dest_file.exists():
        if force:
            print(f'WARNING: existing {dest_file} will be overwritten',
                  file=sys.stderr)
        else:
            print(f'WARNING: skipping import of already imported {src_file}',
                  file=sys.stderr)
            return False
    try:
        bank_statement = parser.parse()
    except NotImplementedError as e:
        print(f'Warning: couldn\'t parse {src_file}:', e.args,
              file=sys.stderr)
        return False
    if not dry_run:
        try:
            with open(dest_file, 'w') as f:
                bank_statement.write_ledger(f)
        except Exception as e:
            # Remove hledger file to allow clean import after fixing
            # whatever caused the Exception.
            try:
                dest_file.unlink()
            except FileNotFoundError:
                pass
            raise e
    else:
        with io.StringIO() as f:
            bank_statement.write_ledger(f)
            print(f.getvalue())
    import_transaction.add_file(dest_file)
    src_ext = src_file.suffix
    moved_src = dest_file.with_suffix(src_ext)
    import_transaction.move_file_to_annex(src_file, moved_src)
    return True

def merge_dateranges(dateranges: list[tuple[date, date]]) -> None:
    dateranges.sort(key=lambda t: t[0])
    for i in reversed(range(len(dateranges)-1)):
        if 0 <= (dateranges[i+1][0] - dateranges[i][1]).days <= 1:
            dateranges[i] = (dateranges[i][0], dateranges[i+1][1])
            dateranges.pop(i+1)

class AddFileTransaction(Protocol):
    def add_files(self, files: Iterable[Union[Path, str]]) -> None: ...

def write_include_files(ledger_root: str, git: AddFileTransaction) -> None:
    ledger_name = 'journal.hledger'
    ledger_files = []
    for(dirpath, dirnames, filenames) in os.walk(ledger_root):
        if dirpath == ledger_root:
            for i in reversed(range(len(dirnames))):
                if not dirnames[i].isnumeric():
                    dirnames.pop(i)
        dirnames.sort()
        ledger = os.path.join(dirpath, ledger_name)
        with open(ledger, 'w') as f:
            print("; autogenerated file, do not edit\n", file=f)
            for d in dirnames:
                print('include', os.path.join(d, ledger_name), file=f)
            try:
                # prevent infinite loop
                filenames.remove(ledger_name)
            except ValueError:
                pass
            filenames = sorted(f for f in filenames if f.endswith('.hledger'))
            for filename in filenames:
                print('include', filename, file=f)
        ledger_files.append(ledger)
    git.add_files(ledger_files)

def read_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    xdg = getXDGdirectories('bank-statement-parser')
    config.read(xdg['config'] / 'import.cfg')
    if 'dirs' not in config:
        config['dirs'] = {}
    dirs = config['dirs']
    if 'ledgers' not in dirs:
        dirs['ledgers'] = '~/accounting/bank_statements'
    dirs['ledgers'] = os.path.expanduser(dirs['ledgers'])
    assert os.path.exists(dirs['ledgers'])
    if 'incoming' not in dirs:
        dirs['incoming'] = os.path.join(dirs['ledgers'], 'incoming')
    dirs['incoming'] = os.path.expanduser(dirs['incoming'])
    assert os.path.exists(dirs['incoming'])
    if 'git' not in config:
        config['git'] = {}
    git_config = config['git']
    if 'git_dir' not in git_config:
        git_config['git_dir'] = os.path.join(dirs['ledgers'], '.git')
    if 'import_branch' not in git_config:
        git_config['import_branch'] = 'import'
    if not os.path.exists(git_config['git_dir']):
        del config['git']
    return config

if __name__ == '__main__':
    aparser = argparse.ArgumentParser(
            description='import account statement PDFs into hledger')
    aparser.add_argument('--force', dest='force', default=False,
                         action='store_true',
                         help='overwrite existing ledgers')
    aparser.add_argument('--dry-run', dest='dry_run',
                         default=False, action='store_true',
                         help='run parsers without writing any output files')
    aparser.add_argument('--regenerate-includes', dest='regenerate_includes',
                         default=False, action='store_true',
                         help='only regenerate include files; don\'t import '
                              'new bank statements')

    args = aparser.parse_args()

    config = read_config()
    # change working directory for git status to work correctly
    os.chdir(config['dirs']['ledgers'])
    git: BaseGit
    if 'git' in config:
        git_dir = config['git']['git_dir']
        git = Git(config['dirs']['ledgers'], git_dir)
        import_branch = config['git']['import_branch']
    else:
        git_dir = ''
        git = FakeGit()
        import_branch = git.current_branch()

    if args.regenerate_includes:
        write_include_files(config['dirs']['ledgers'], git)
    else:
        try:
            import_incoming_statements(config['dirs'], git, import_branch,
                                       args.force, args.dry_run)
        except DirtyWorkingDirectoryException:
            print(f'{Path(git_dir).parent} contains uncommitted changes,'
                  ' please commit those before continuing.', file=sys.stderr)
            exit(1)
        # TODO: merge import_branch into default_branch

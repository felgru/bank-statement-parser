#!/usr/bin/python3

# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
import io
import os
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Optional, Protocol, Union

from config import ImportConfig
from git import BaseGit, FakeGit, Git, GitMergeConflictError
from import_transaction import (
        DirtyWorkingDirectoryException,
        import_transaction,
        ImportTransactionProtocol,
        )
from parsers import parsers
from parsers.parser import BankStatementMetadata, Parser
from xdg_dirs import getXDGdirectories


@dataclass
class IncomingStatement:
    statement_path: Path
    parser: Parser
    metadata: BankStatementMetadata


def get_metadata_of_incoming_statements(incoming_dir: Path,
                                        ) -> list[IncomingStatement]:
    incoming_statements = []
    for bankpath in sorted(incoming_dir.iterdir()):
        if not bankpath.is_dir():
            continue
        bank = bankpath.name
        bank_parsers = parsers.get(bank)
        if bank_parsers is None:
            print('unknown bank:', bank, file=sys.stderr)
            continue
        filenames = sorted(bankpath.iterdir())
        if filenames:
            print('importing bank statements from', bank)
        for src_file in filenames:
            try:
                extension = src_file.suffix.lower()
                Parser = bank_parsers[extension]
            except KeyError:
                continue
            parser = Parser(src_file)
            m = parser.parse_metadata()
            print(f'{m.start_date} → {m.end_date}: {src_file}')
            incoming_statements.append(IncomingStatement(
                statement_path=src_file,
                parser=parser,
                metadata=m,
                ))
    return incoming_statements


def sort_incoming_statements_to_ledger_dirs(
        incoming_statements: list[IncomingStatement],
        classify: Callable[[BankStatementMetadata], str],
        ) -> dict[str, list[IncomingStatement]]:
    classified: dict[str, list[IncomingStatement]] = defaultdict(list)
    for statement in incoming_statements:
        ledger = classify(statement.metadata)
        classified[ledger].append(statement)
    return classified


def import_incoming_statements(incoming_statements: list[IncomingStatement],
                               ledger_dir: Path,
                               git: BaseGit,
                               import_branch: str,
                               force: bool,
                               dry_run: bool) -> None:
    rules_dir = ledger_dir / 'rules'
    with import_transaction(git, import_branch, dry_run) as transaction:
        import_summary = dict()
        by_bank: dict[str, list[IncomingStatement]] = defaultdict(list)
        for incoming in incoming_statements:
            by_bank[incoming.parser.bank_folder].append(incoming)
        for bank, incoming_statements in by_bank.items():
            dateranges = []
            imported_files = []
            for incoming in incoming_statements:
                src_file = incoming.statement_path
                f = src_file.name
                m = incoming.metadata
                mid_date = m.start_date + (m.end_date - m.start_date) / 2
                year = str(mid_date.year)
                if m.end_date - m.start_date <= timedelta(weeks=6):
                    month = str(mid_date.month).zfill(2)
                    dest_dir = ledger_dir / year / month / bank
                else:
                    dest_dir = ledger_dir / year / bank
                if not dry_run:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = (dest_dir / f).with_suffix('.hledger')
                if parse_and_write_bank_statement(incoming.parser,
                                                  src_file, dest_file,
                                                  rules_dir,
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
            write_include_files(ledger_dir, transaction)
        if import_summary:
            commit_message = 'import bank statements\n\n'
            commit_message += '\n\n'.join(s for _, s in sorted(import_summary
                                                               .items()))
            transaction.set_commit_message(commit_message)

def parse_and_write_bank_statement(
        parser: Parser,
        src_file: Path,
        dest_file: Path,
        rules_dir: Optional[Path],
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
        bank_statement = parser.parse(rules_dir=rules_dir)
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

def write_include_files(ledger_root: Path, git: AddFileTransaction) -> None:
    ledger_name = 'journal.hledger'
    ledger_files = []
    for(dirpath, dirnames, filenames) in os.walk(ledger_root):
        if Path(dirpath) == ledger_root:
            for i in reversed(range(len(dirnames))):
                if not dirnames[i].isnumeric():
                    dirnames.pop(i)
        dirnames.sort()
        ledger = Path(dirpath, ledger_name)
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


def regenerate_includes(work_dir: Path, config: ImportConfig) -> None:
    for ledger_config in config.ledgers.values():
        if work_dir.is_relative_to(ledger_config.ledger_dir):
            break
    else:
        print(f'Current working directory {work_dir} is not inside any known'
              ' ledger path.')
        exit(1)

    print(f'Regenerate includes in {ledger_config.ledger_dir}.')
    # change working directory for git status to work correctly
    os.chdir(ledger_config.ledger_dir)
    git: BaseGit
    if ledger_config.git_dir is not None:
        git = Git(ledger_config.ledger_dir, ledger_config.git_dir)
        import_branch = ledger_config.import_branch
    else:
        git = FakeGit()
        import_branch = git.current_branch()

    write_include_files(ledger_config.ledger_dir, git)


def main() -> None:
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
    aparser.add_argument('--no-merge', dest='merge',
                         default=True, action='store_false',
                         help='don\'t merge import branch after import')

    args = aparser.parse_args()

    xdg = getXDGdirectories('bank-statement-parser')
    config_file = xdg['config'] / 'import.cfg'
    config = ImportConfig.read_from_file(config_file)

    if args.regenerate_includes:
        regenerate_includes(Path.cwd(), config)
        exit(0)

    selection_script = xdg['config'] / 'select_ledger.py'
    select_ledger: Callable[[BankStatementMetadata], str]
    if selection_script.exists():
        with open(selection_script, 'r') as f:
            content = f.read()
            parse_globals: dict[str, Any] = {
                'BankStatementMetadata': BankStatementMetadata,
                }
            exec(compile(content, selection_script, 'exec'), parse_globals)
            if 'select_ledger' not in parse_globals:
                print(f'{selection_script} doesn\'t contain select_ledger'
                      ' function.',
                      file=sys.stderr)
                exit(1)
            select_ledger = parse_globals['select_ledger']
    elif len(config.ledgers) == 1:
        ledger_name = next(iter(config.ledgers))
        def select_ledger(meta: BankStatementMetadata) -> str:
            return ledger_name
    else:
        print(f'Error: {config_file} contains more than one ledger,'
              f' but {selection_script} is missing.',
              file=sys.stderr)
        exit(1)
    incoming_statements = get_metadata_of_incoming_statements(
            config.incoming_dir)
    classified = sort_incoming_statements_to_ledger_dirs(
            incoming_statements,
            select_ledger,
            )
    if any(key not in config.ledgers for key in classified.keys()):
        for key, statements in classified.items():
            if key in config.ledgers:
                continue
            mismatched_files = ', '.join(str(s.statement_path)
                                         for s in statements)
            print(f'Error: {mismatched_files} were assigned to unknown ledger'
                  f' configuration {key}. Please check {selection_script}.',
                  file=sys.stderr)
        exit(1)
    for key, statements in classified.items():
        ledger_config = config.ledgers[key]
        print(f'Importing bank statements to {ledger_config.ledger_dir}.')
        # change working directory for git status to work correctly
        os.chdir(ledger_config.ledger_dir)
        git: BaseGit
        if ledger_config.git_dir is not None:
            git = Git(ledger_config.ledger_dir, ledger_config.git_dir)
            import_branch = ledger_config.import_branch
        else:
            git = FakeGit()
            import_branch = git.current_branch()

        try:
            import_incoming_statements(statements,
                                       ledger_config.ledger_dir,
                                       git, import_branch,
                                       args.force, args.dry_run)
        except DirtyWorkingDirectoryException:
            print(f'{ledger_config.ledger_dir} contains uncommitted changes,'
                  ' please commit those before continuing.', file=sys.stderr)
            exit(1)
        # The import_transaction in import_incoming_statements automatically
        # resets the branch to the previously checked-out one after importing
        # to the import_branch.
        if (args.merge
                and isinstance(git, Git)
                and import_branch != git.current_branch()):
            try:
                git.merge(import_branch)
            except GitMergeConflictError as e:
                conflicting_files = [ledger_config.ledger_dir / c.name
                                     for c in e.conflicts]
                not_autogenerated = [p for p in conflicting_files
                                     if p.name != 'journal.hledger']
                if not_autogenerated:
                    raise RuntimeError(
                            'Could not automerge the following files:\n'
                            + '\n'.join(str(p) for p in not_autogenerated))
                write_include_files(ledger_config.ledger_dir, git)
                git.commit(f"Merge branch '{import_branch}'")


if __name__ == '__main__':
    main()

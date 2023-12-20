#!/usr/bin/python3

# SPDX-FileCopyrightText: 2019–2023 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
import os
from pathlib import Path
import sys
from typing import Any, Optional

from bank_statement import BankStatementMetadata
from config import ImportConfig, LedgerConfig
from git import (
        BaseGit,
        FakeGit,
        Git,
        GitEmptyCommitError,
        GitMergeConflictError,
        )
from import_transaction import (
        DirtyWorkingDirectoryException,
        import_transaction,
        ImportTransactionProtocol,
        )
from include_files import write_include_files
from parsers import parsers
from parsers.parser import BaseParserConfig, Parser
from utils import UserError
from utils.dates import merge_dateranges
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


class ParserConfigs:
    def __init__(self, ledger_dir: Path):
        self.rules_dir = ledger_dir / 'rules'
        self.by_config_type = ParserConfigsForConfigTypes(ledger_dir)

    def __getitem__(self, key: type[Parser]) -> BaseParserConfig:
        return self.by_config_type[key.config_type()]


class ParserConfigsForConfigTypes(defaultdict[type[BaseParserConfig],
                                              BaseParserConfig]):
    def __init__(self, ledger_dir: Path):
        self.rules_dir = ledger_dir / 'rules'

    def __missing__(self, key: type[BaseParserConfig]) -> BaseParserConfig:
        config = key.load(self.rules_dir)
        self[key] = config
        return config


def import_incoming_statements(incoming_statements: list[IncomingStatement],
                               ledger_dir: Path,
                               git: BaseGit,
                               import_branch: str,
                               force: bool,
                               dry_run: bool) -> None:
    configs = ParserConfigs(ledger_dir)
    with import_transaction(git, import_branch, dry_run) as transaction:
        import_summary = dict()
        by_bank: dict[str, list[IncomingStatement]] = defaultdict(list)
        for incoming in incoming_statements:
            by_bank[incoming.parser.config_type().bank_folder].append(incoming)
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
                config = configs[type(incoming.parser)]
                if parse_and_write_bank_statement(incoming.parser,
                                                  src_file, dest_file,
                                                  config,
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
        parser_config: BaseParserConfig,
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
        bank_statement = parser.parse(parser_config)
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
        bank_statement.write_ledger(sys.stdout)
    import_transaction.add_file(dest_file)
    src_ext = src_file.suffix
    moved_src = dest_file.with_suffix(src_ext)
    import_transaction.move_file_to_annex(src_file, moved_src)
    return True


def get_ledger_config_containing_dir(work_dir: Path,
                                     config: ImportConfig) -> LedgerConfig:
    for ledger_config in config.ledgers.values():
        if work_dir.is_relative_to(ledger_config.ledger_dir):
            return ledger_config
    else:
        print(f'Current working directory {work_dir} is not inside any known'
              ' ledger path.', file=sys.stderr)
        exit(1)


def regenerate_includes(work_dir: Path, config: ImportConfig) -> None:
    ledger_config = get_ledger_config_containing_dir(work_dir, config)
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


class Main:
    def __init__(self) -> None:
        aparser = argparse.ArgumentParser(
                description='import account statement PDFs into hledger')
        aparser.add_argument('--force', dest='force', default=False,
                             action='store_true',
                             help='overwrite existing ledgers')
        aparser.add_argument('--dry-run', dest='dry_run',
                             default=False, action='store_true',
                             help='run parsers without writing any output files')
        subcommands = aparser.add_mutually_exclusive_group()
        subcommands.add_argument(
                '--regenerate-includes',
                dest='regenerate_includes',
                default=False,
                action='store_true',
                help='only regenerate include files; don\'t import '
                     'new bank statements',
        )
        banks = sorted(parsers)
        subcommands.add_argument(
                '--reimport',
                default=None,
                choices=['all', *banks],
                help='reimport bank statements from given bank; '
                     'argument can be "all" to reimport from all banks',
        )
        subcommands.add_argument(
                '--create-accounts-cfg',
                default=None,
                nargs='*',
                choices=banks,
                help='create accounts config with default mapping for given '
                     'bank(s)',
        )
        aparser.add_argument('--no-merge', dest='merge',
                             default=True, action='store_false',
                             help='don\'t merge import branch after import')

        self.args = aparser.parse_args()

        self.xdg = getXDGdirectories('bank-statement-parser')
        self.config_file = self.xdg['config'] / 'import.cfg'
        self.config = ImportConfig.read_from_file(self.config_file)

        mode: Callable[[], None]
        if self.args.regenerate_includes:
            mode = self.regenerate_includes
        elif self.args.reimport is not None:
            mode = self.reimport_bank_statements
        elif self.args.create_accounts_cfg is not None:
            mode = self.create_accounts_cfg
        else:
            mode = self.import_new_bank_statements
        self.selected_mode = mode

    def run(self) -> None:
        try:
            self.selected_mode()
        except UserError as e:
            print(e.msg, file=sys.stderr)
            exit(1)

    def regenerate_includes(self) -> None:
        regenerate_includes(Path.cwd(), self.config)

    def import_new_bank_statements(self) -> None:
        selection_script = self.xdg['config'] / 'select_ledger.py'
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
        elif len(self.config.ledgers) == 1:
            ledger_name = next(iter(self.config.ledgers))
            def select_ledger(meta: BankStatementMetadata) -> str:
                return ledger_name
        else:
            print(f'Error: {self.config_file} contains more than one ledger,'
                  f' but {selection_script} is missing.',
                  file=sys.stderr)
            exit(1)
        incoming_statements = get_metadata_of_incoming_statements(
                self.config.incoming_dir)
        classified = sort_incoming_statements_to_ledger_dirs(
                incoming_statements,
                select_ledger,
                )
        if any(key not in self.config.ledgers for key in classified.keys()):
            for key, statements in classified.items():
                if key in self.config.ledgers:
                    continue
                mismatched_files = ', '.join(str(s.statement_path)
                                             for s in statements)
                print(f'Error: {mismatched_files} were assigned to unknown ledger'
                      f' configuration {key}. Please check {selection_script}.',
                      file=sys.stderr)
            exit(1)
        for key, statements in classified.items():
            ledger_config = self.config.ledgers[key]
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
                                           self.args.force, self.args.dry_run)
            except DirtyWorkingDirectoryException:
                print(f'{ledger_config.ledger_dir} contains uncommitted changes,'
                      ' please commit those before continuing.', file=sys.stderr)
                exit(1)
            # The import_transaction in import_incoming_statements automatically
            # resets the branch to the previously checked-out one after importing
            # to the import_branch.
            if (self.args.merge
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

    def reimport_bank_statements(self) -> None:
        bank_name = self.args.reimport
        if bank_name == 'all':
            selected_parsers = dict(parsers.items())
        else:
            selected_parser = parsers.get(bank_name)
            if selected_parser is None:
                print(f'Unknown bank name: {bank_name}.', file=sys.stderr)
                exit(1)
            selected_parsers = {bank_name: selected_parser}
            del selected_parser
        ledger_config = get_ledger_config_containing_dir(Path.cwd(),
                                                         self.config)
        print(f'Reimport bank statements in {ledger_config.ledger_dir}.')
        # change working directory for git status to work correctly
        os.chdir(ledger_config.ledger_dir)
        git: BaseGit
        if ledger_config.git_dir is not None:
            git = Git(ledger_config.ledger_dir, ledger_config.git_dir)
            import_branch = ledger_config.import_branch
        else:
            git = FakeGit()
            import_branch = git.current_branch()

        parser_configs = ParserConfigs(ledger_config.ledger_dir)

        try:
            with import_transaction(git, import_branch,
                                    self.args.dry_run) as transaction:
                for year in sorted(dir for dir in ledger_config.ledger_dir \
                                                               .iterdir()
                                   if dir.is_dir() and dir.name.isnumeric()):
                    for subdir in sorted(dir for dir in year.iterdir()
                                         if dir.is_dir()):
                        if subdir.name.isnumeric(): # month
                            updated_ledgers: list[Path] = []
                            for bankdir in sorted(dir
                                                  for dir in subdir.iterdir()
                                                  if dir.is_dir()):
                                updated_ledgers.extend(
                                    self.reimport_bank_dir(bankdir,
                                                           selected_parsers,
                                                           parser_configs))
                        else:
                            updated_ledgers = \
                                self.reimport_bank_dir(subdir,
                                                       selected_parsers,
                                                       parser_configs)
                        git.add_files(updated_ledgers)
                commit_message = f'reimport {bank_name} bank statements'
                transaction.set_commit_message(commit_message)
        except GitEmptyCommitError:
            print('Nothing changed with the reimport.')
            return

        # The import_transaction automatically resets the branch to the
        # previously checked-out one after importing to the import_branch.
        if (self.args.merge
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


    def reimport_bank_dir(self,
                          bank_dir: Path,
                          parsers: dict[str, dict[str, type[Parser]]],
                          parser_configs: dict[type[Parser], BaseParserConfig],
                          ) -> list[Path]:
        bank_parsers = parsers.get(bank_dir.name)
        if bank_parsers is None:
            return []
        files_with_parser = sorted(
            (f, parser)
            for f in bank_dir.iterdir()
            if f.is_file()
               and (parser := bank_parsers.get(f.suffix.lower())) is not None
        )
        reimported_ledgers: list[Path] = []
        for file_, parser in files_with_parser:
            bank_statement = parser(file_).parse(parser_configs[parser])
            ledger_file = file_.with_suffix('.hledger')
            if not ledger_file.exists():
                continue
            print(f'Reimporting {file_}')
            with ledger_file.open('w') as f:
                bank_statement.write_ledger(f)
            reimported_ledgers.append(ledger_file)
        return reimported_ledgers

    def create_accounts_cfg(self) -> None:
        bank_names = self.args.create_accounts_cfg
        if not bank_names:
            print('please specify bank to create accounts config for.',
                  file=sys.stderr)
            exit(1)
        selected_parsers = {bank_name: parsers.get(bank_name)
                            for bank_name in bank_names}
        ledger_config = get_ledger_config_containing_dir(Path.cwd(),
                                                         self.config)
        print(f'Create accounts config in {ledger_config.ledger_dir}.')
        # change working directory for git status to work correctly
        os.chdir(ledger_config.ledger_dir)
        #git: BaseGit
        #if ledger_config.git_dir is not None:
        #    git = Git(ledger_config.ledger_dir, ledger_config.git_dir)
        #    import_branch = ledger_config.import_branch
        #else:
        #    git = FakeGit()
        #    import_branch = git.current_branch()

        parser_configs = ParserConfigs(ledger_config.ledger_dir)
        for bank_name, parsers_ in selected_parsers.items():
            unique_configs = set(parser_configs[parser]
                                 for parser in parsers_.values())
            if len(unique_configs) != 1:
                print(f'Parser config for {bank_name} is not unique.')
                exit(1)
            parser_config = next(iter(unique_configs))
            print(f'Write accounts mapping for {bank_name}.')
            parser_config.store(parser_configs.rules_dir)


if __name__ == '__main__':
    Main().run()

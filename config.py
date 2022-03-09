# SPDX-FileCopyrightText: 2019–2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
import configparser
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional


@dataclass
class LedgerConfig:
    ledger_dir: Path
    git_dir: Optional[Path]
    import_branch: str

    @classmethod
    def from_config(cls,
                    config: configparser.SectionProxy,
                    ) -> LedgerConfig:
        ledger_dir = Path(os.path.expanduser(config.get('ledger_dir')))
        assert ledger_dir.exists(), \
                f'Ledger directory {ledger_dir} does not exist.'
        git_dir_str = config.get('git_dir')
        if git_dir_str is not None:
            git_dir = Path(os.path.expanduser(git_dir_str))
        else:
            git_dir = ledger_dir / '.git'
        import_branch = config.get('import_branch', 'import')
        return cls(ledger_dir=ledger_dir,
                   git_dir=git_dir if git_dir.exists() else None,
                   import_branch=import_branch)


@dataclass
class ImportConfig:
    incoming_dir: Path
    ledgers: list[LedgerConfig]

    @classmethod
    def read_from_file(cls, config_file: Path) -> ImportConfig:
        config = configparser.ConfigParser()
        config.read(config_file)
        default_incoming_dir = '~/accounting/incoming'
        try:
            common_section = config.pop('common')
            incoming_str = common_section.get('incoming_dir',
                                              default_incoming_dir)
        except KeyError:
            incoming_str = default_incoming_dir
        incoming_dir = Path(os.path.expanduser(incoming_str))
        assert incoming_dir.exists(), \
                f'Incoming directory {incoming_dir} does not exist.'
        ledgers = []
        for ledger_name, ledger_config in config.items():
            ledgers.append(LedgerConfig.from_config(ledger_config))
        assert len(ledgers) > 1, 'Missing configuration for ledger directories.'
        return cls(incoming_dir=incoming_dir, ledgers=ledgers)

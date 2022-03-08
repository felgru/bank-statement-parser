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
class ImportConfigDirs:
    ledgers: Path
    incoming: Path

    @classmethod
    def from_config(cls, config: configparser.ConfigParser) -> ImportConfigDirs:
        if 'dirs' not in config:
            config['dirs'] = {}
        dirs = config['dirs']
        ledgers = Path(os.path.expanduser(
            dirs.get('ledgers', '~/accounting/bank_statements')))
        assert ledgers.exists(), f'Ledgers directory {ledgers} does not exist.'
        if 'incoming' in dirs:
            incoming = Path(os.path.expanduser(dirs['incoming']))
        else:
            incoming = ledgers / 'incoming'
        assert incoming.exists(), \
                f'Incoming directory {incoming} does not exist.'
        return cls(ledgers=ledgers, incoming=incoming)


@dataclass
class ImportConfigGit:
    git_dir: Path
    import_branch: str

    @classmethod
    def from_config(cls,
                    config: configparser.ConfigParser,
                    dirs: ImportConfigDirs) -> Optional[ImportConfigGit]:
        if 'git' not in config:
            config['git'] = {}
        git_config = config['git']
        git_dir = Path(git_config.get('git_dir', str(dirs.ledgers / '.git')))
        import_branch = git_config.get('import_branch', 'import')
        if not git_dir.exists():
            return None
        else:
            return cls(git_dir=git_dir, import_branch=import_branch)


@dataclass
class ImportConfig:
    dirs: ImportConfigDirs
    git: Optional[ImportConfigGit]

    @classmethod
    def read_from_file(cls, config_file: Path) -> ImportConfig:
        config = configparser.ConfigParser()
        config.read(config_file)
        dirs = ImportConfigDirs.from_config(config)
        git = ImportConfigGit.from_config(config, dirs)
        return cls(dirs=dirs, git=git)

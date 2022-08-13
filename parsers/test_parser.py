# SPDX-FileCopyrightText: 2022 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections.abc import Callable
from pathlib import Path

import pytest

from .parser import GenericParserConfig, ParserConfigError


class ExampleParserConfig(GenericParserConfig):
    bank_folder = 'test'
    bank_name = 'Test bank'
    DEFAULT_ACCOUNTS = {
        'my account': 'assets:bank:test',
        'Another Account': 'expenses:test',
        '1234': 'expenses:test1234',
    }


@pytest.fixture
def create_test_config(tmp_path: Path) -> Callable[[dict[str, str]], Path]:
    def create_test_config(accounts: dict[str, str]) -> Path:
        test_dir = tmp_path / ExampleParserConfig.bank_folder
        test_dir.mkdir()
        with open(test_dir / 'accounts.cfg', 'w') as f:
            f.write('[accounts]\n')
            for k, v in accounts.items():
                f.write(f'{k} = {v}\n')
        return tmp_path

    return create_test_config


def test_default_config(tmp_path: Path) -> None:
    config = ExampleParserConfig.load(tmp_path)
    assert config.accounts == config.DEFAULT_ACCOUNTS


def test_loading_all_accounts(
        create_test_config: Callable[[dict[str, str]], Path],
        ) -> None:
    test_accounts = {
        'my account': 'my_assets:bank:test',
        'Another Account': 'my_expenses:test',
        '1234': 'my_expenses:test1234',
    }
    config_path = create_test_config(test_accounts)
    config = ExampleParserConfig.load(config_path)
    assert config.accounts == test_accounts


def test_fallback_to_default_accounts(
        create_test_config: Callable[[dict[str, str]], Path],
        ) -> None:
    test_accounts = {
        'Another Account': 'my_expenses:test',
        '1234': 'my_expenses:test1234',
    }
    config_path = create_test_config(test_accounts)
    config = ExampleParserConfig.load(config_path)
    expected_accounts = {
        'my account': 'assets:bank:test',
        **test_accounts
    }
    assert config.accounts == expected_accounts


def test_error_on_unknown_account(
        create_test_config: Callable[[dict[str, str]], Path],
        ) -> None:
    test_accounts = {
        'unknown account': 'expenses:unknown account',
    }
    config_path = create_test_config(test_accounts)
    with pytest.raises(ParserConfigError):
        ExampleParserConfig.load(config_path)

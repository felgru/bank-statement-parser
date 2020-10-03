# SPDX-FileCopyrightText: 2019–2020 Felix Gruber <felgru@posteo.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
from typing import Dict

def getXDGdirectories(basename: str) -> Dict[str, str]:
    if sys.platform == 'linux':
        # XDG base dirs
        # https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
        xdg_dirs = {
                'cache': os.environ.get('XDG_CACHE_HOME',
                                        os.environ['HOME'] + '/.cache'),
                'config': os.environ.get('XDG_CONFIG_HOME',
                                         os.environ['HOME'] + '/.config'),
                'data': os.environ.get('XDG_DATA_HOME',
                                       os.environ['HOME'] + '/.local/share'),
               }
    elif sys.platform == 'darwin':
        # MacOS-specific directories
        xdg_dirs = {
                'cache': os.environ['HOME'] + '/Library/Caches',
                'config': os.environ['HOME'] + '/Library/Preferences',
                'data': os.environ['HOME'] + '/Library/Application Support',
                }
    else:
        raise RuntimeError(f'unknown operating system: {sys.platform}')
    return {k: v + '/' + basename for k, v in xdg_dirs.items()}

import os
from typing import Dict

def getXDGdirectories(basename: str) -> Dict[str, str]:
    xdg_dirs = {
            'cache': os.environ.get('XDG_CACHE_HOME',
                                    os.environ['HOME'] + '/.cache'),
            'config': os.environ.get('XDG_CONFIG_HOME',
                                     os.environ['HOME'] + '/.config'),
            'data': os.environ.get('XDG_DATA_HOME',
                                   os.environ['HOME'] + '/.local/share'),
           }
    return {k: v + '/' + basename for k, v in xdg_dirs.items()}

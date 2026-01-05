from pathlib import Path
from typing import Dict


def list_files(directory, extensions=None) -> Dict[str, str]:
    """
    List all the files in a directory with the given extensions.
    :param directory:
    :param extensions:
    :return:
    """
    if extensions is None:
        return {p.name: str(p) for p in Path(directory).iterdir() if p.is_file()}
    else:
        return {
            p.name: str(p)
            for p in Path(directory).iterdir()
            if p.is_file() and p.suffix in extensions
        }

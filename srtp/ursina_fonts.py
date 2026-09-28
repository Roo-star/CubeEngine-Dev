"""Font values Ursina can load, whatever font file the Project uses.

Ursina 8 resolves fonts only by file name inside its own font/asset folders
(an absolute or Panda3D-style path yields "missing font" and then a crash).
Fonts are therefore copied once into a CubeEngine cache folder, named by
content hash, and that folder is made Ursina's fonts folder.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Union

_DEFAULT_CACHE = Path(tempfile.gettempdir()) / "cubeengine_ursina_fonts"


def ursina_font(path: Union[str, Path], cache: Optional[Path] = None) -> str:
    from ursina import application
    source = Path(path)
    folder = Path(cache) if cache is not None else _DEFAULT_CACHE
    folder.mkdir(parents=True, exist_ok=True)
    application.fonts_folder = folder
    # Ursina 7 hands the bare name straight to Panda3D's loader, which only
    # searches the model path, so the cache folder must be on it as well.
    from panda3d.core import Filename, getModelPath
    folder_name = Filename.from_os_specific(str(folder.resolve()))
    if not any(getModelPath().getDirectory(i) == folder_name for i in range(getModelPath().getNumDirectories())):
        getModelPath().appendDirectory(folder_name)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    name = "{0}_{1}".format(digest, re.sub(r"[^A-Za-z0-9._-]", "_", source.name))
    target = folder / name
    if not target.is_file():
        shutil.copyfile(source, target)
    return name

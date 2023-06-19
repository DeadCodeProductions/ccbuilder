from pathlib import Path
from ccbuilder.utils.utils import initialize_repos, initialize_patches_dir
from diopter.repository import DEFAULT_REPOS_DIR


DEFAULT_PREFIX_PARENT_PATH = Path.home() / ".cache" / "ccbuilder-compilers"
DEFAULT_PATCH_DIR = Path.home() / ".cache" / "ccbuilder-patches"


__all__ = ["DEFAULT_PATCH_DIR", "DEFAULT_PREFIX_PARENT_PATH", "DEFAULT_REPOS_DIR"]

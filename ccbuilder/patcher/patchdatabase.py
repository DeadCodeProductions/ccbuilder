from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

from ccbuilder.utils.utils import CompilerProject

T = TypeVar("T")


def _save_db(func: Callable[..., T]) -> Callable[..., T]:
    def save_decorator(db: PatchDB, *args: list[Any], **kwargs: dict[Any, Any]) -> T:

        res = func(db, *args, **kwargs)
        with open(db.path, "w") as f:
            json.dump(db.data, f, indent=4)
        return res

    return save_decorator


class PatchDB:
    def __init__(
        self,
        path_to_db: Path = Path.home()
        / ".cache"
        / "ccbuilder-patches"
        / "patchdb.json",
    ):
        default = Path.home() / ".cache" / "ccbuilder-patches" / "patchdb.json"
        if path_to_db == default:
            if not path_to_db.exists():
                from shutil import copy

                logging.warning(
                    f"Missing default patch database. Recreating it at {default}..."
                )
                os.makedirs(path_to_db.parent, exist_ok=True)
                for entry in (Path(__file__).parent.parent / "patches").iterdir():
                    if not (default.parent / entry.name).exists():
                        copy(entry, default.parent / entry.name)

        self.path = path_to_db.absolute()
        self.patch_path_prefix = self.path.parent
        self.data: dict[str, Any] = {}
        with open(self.path, "r") as f:
            self.data = json.load(f)

    @_save_db
    def save(self, patch: Path, commits: list[str]) -> None:
        # To not be computer dependend, just work with the name of the patch
        patch_basename = os.path.basename(patch)
        logging.debug(f"Saving entry for {patch_basename}: {commits}")

        if patch_basename not in self.data:
            self.data[patch_basename] = commits
        else:
            self.data[patch_basename].extend(commits)

        # Make entries unique
        self.data[patch_basename] = list(set(self.data[patch_basename]))

    # TODO: make a user friendly variant before using it in ccbuilder
    @_save_db
    def save_bad(
        self,
        patches: list[Path],
        commit: str,
        project: CompilerProject,
    ) -> None:
        logging.debug(f"Saving bad: {project.to_string()} {commit} {patches}")
        patches_str = [str(os.path.basename(patch)) for patch in patches]

        if "bad" not in self.data:
            self.data["bad"] = {}

        if project.to_string() not in self.data["bad"]:
            self.data["bad"][project.to_string()] = {}

        if commit not in self.data["bad"][project.to_string()]:
            self.data["bad"][project.to_string()][commit] = []

        self.data["bad"][project.to_string()][commit].append(patches_str)

    @_save_db
    def clear_bad(
        self,
        patches: list[Path],
        commit: str,
        project: CompilerProject,
    ) -> None:
        logging.debug(f"Clearing bad: {project.to_string()} {commit} {patches}")
        patches_str = [str(os.path.basename(patch)) for patch in patches]

        if (
            "bad" not in self.data
            or project.to_string() not in self.data["bad"]
            or commit not in self.data["bad"][project.to_string()]
        ):
            return

        good_hash = hash("".join(patches_str))
        list_bad = self.data["bad"][project.to_string()][commit]
        list_bad = [combo for combo in list_bad if hash("".join(combo)) != good_hash]

        self.data["bad"][project.to_string()][commit] = list_bad

    def is_known_bad(
        self,
        patches: list[Path],
        commit: str,
        project: CompilerProject,
    ) -> bool:
        """Checks if a given compiler-commit-patches combination
        has already been tested and failed to build.

        Args:
        self:
        patches (list[Path]): patches
        commit (str): commit
        compiler_config (NestedNamespace): compiler_config

        Returns:
        bool:
        """
        patches_str = [str(os.path.basename(patch)) for patch in patches]

        if "bad" not in self.data:
            return False

        if project.to_string() not in self.data["bad"]:
            return False

        if commit not in self.data["bad"][project.to_string()]:
            return False

        current_hash = hash("".join(patches_str))
        for known_bad in self.data["bad"][project.to_string()][commit]:
            if current_hash == hash("".join(sorted(known_bad))):
                return True

        return False

    def required_patches(self, commit: str) -> list[Path]:
        """Get the known required patches form the database.

        Args:
            self:
            commit (str): commit

        Returns:
            list[Path]: List of known required patches.
        """

        required_patches: list[Path] = []
        for patch, patch_commits in self.data.items():
            if commit in patch_commits:
                required_patches.append(self.patch_path_prefix / patch)
        return required_patches

    def requires_this_patch(self, commit: str, patch: Path) -> bool:
        patch_basename = os.path.basename(patch)
        if patch_basename not in self.data:
            return False
        else:
            return commit in self.data[patch_basename]

    def requires_all_these_patches(self, commit: str, patches: list[Path]) -> bool:
        patch_basenames = [os.path.basename(patch) for patch in patches]
        if any(patch_basename not in self.data for patch_basename in patch_basenames):
            return False
        else:
            return all(
                commit in self.data[patch_basename]
                for patch_basename in patch_basenames
            )

    @_save_db
    def manual_intervention_required(self, project: CompilerProject, rev: str) -> None:
        if "manual" not in self.data:
            self.data["manual"] = []

        self.data["manual"].append(f"{project.to_string()} {rev}")
        self.data["manual"] = list(set(self.data["manual"]))

    def in_manual(self, project: CompilerProject, rev: str) -> bool:
        if "manual" not in self.data:
            return False
        else:
            return f"{project.to_string()} {rev}" in self.data["manual"]

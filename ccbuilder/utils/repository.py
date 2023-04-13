from __future__ import annotations

import logging
import os
import subprocess
from functools import cache
from pathlib import Path
from typing import Optional

import ccbuilder.utils.utils as utils

from ccbuilder.defaults import DEFAULT_REPOS_DIR


class RepositoryException(Exception):
    pass


Revision = str
Commit = str


class Repo:
    def __init__(self, path: Path, main_branch: str):
        self.path = os.path.abspath(path)
        self.main_branch = main_branch

    @cache
    def get_best_common_ancestor(self, rev_a: Revision, rev_b: Revision) -> str:
        a = self.rev_to_commit(rev_a)
        b = self.rev_to_commit(rev_b)
        return utils.run_cmd(
            f"git -C {self.path} merge-base {a} {b}", capture_output=True
        )

    @cache
    def rev_to_commit(self, rev: Revision) -> Commit:
        """Convert any revision (commits, tags etc.) into their
        SHA1 hash via git rev-parse.

        Args:
            rev (str): Revision to convert.

        Returns:
            str: Hash of `rev` in this repo.
        """
        # Could support list of revs...
        try:
            if rev == "trunk" or rev == "master" or rev == "main":
                rev = self.main_branch
            logging.debug(f"git -C {self.path} rev-parse {rev}")
            return utils.run_cmd(
                f"git -C {self.path} rev-parse {rev}", capture_output=True
            )
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

    def rev_to_range_needing_patch(
        self, introducer: Revision, fixer: Revision
    ) -> list[Commit]:
        """
        This function's aim is best described with a picture
           O---------P
          /   G---H   \      I---J       L--M
         /   /     \   \    /     \     /
        A---B---Z---C---N---D-------E---F---K
             \     /
              Q---R
        call rev_to_range_needing_patch(G, K) gives
        (K, F, 'I, J, D, E', C, H, G)
        in particular it doesn't include Z, P, O, Q and R
        Range G~..K would include these

        Args:
            introducer (str): introducer commit
            fixer (str): fixer commit

        Returns:
            list[str]: List of revision hashes needing the patch.
        """
        #

        # Get all commits with at least 2 parents
        try:
            merges_after_introducer = utils.run_cmd(
                f"git -C {self.path} rev-list --merges {introducer}~..{fixer}",
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

        if len(merges_after_introducer) > 0:
            # Get all parent commits of these (so for C it would be H, Z and R)
            cmd = f"git -C {self.path} rev-parse " + "^@ ".join(merges_after_introducer)
            try:
                merger_parents = set(
                    utils.run_cmd(cmd, capture_output=True).split("\n")
                )
            except subprocess.CalledProcessError as e:
                raise RepositoryException(e)

            # Remove all parents which are child of the requested commit
            unwanted_merger_parents = [
                parent
                for parent in merger_parents
                if not self.is_ancestor(introducer, parent)
            ]
        else:
            unwanted_merger_parents = []
        cmd = f"git -C {self.path} rev-list {fixer} ^{introducer} " + " ^".join(
            unwanted_merger_parents
        )
        try:
            res = [
                commit
                for commit in utils.run_cmd(cmd, capture_output=True).split("\n")
                if commit != ""
            ] + [introducer]
            return res
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

    def direct_first_parent_path(
        self, older: Revision, younger: Revision
    ) -> list[Commit]:
        """Get interval of commits [younger, older] always following the
        first parent.

        Args:
            self:
            older (Revision): Older commit
            younger (Revision): Younger commit

        Returns:
            list[Commit]: Commits [younger, older] following the first parent.
        """
        cmd = f"git -C {self.path} rev-list --first-parent {younger} ^{older}"
        try:
            res = [
                commit
                for commit in utils.run_cmd(cmd, capture_output=True).split("\n")
                if commit != ""
            ] + [older]
            return res
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

    def rev_to_commit_list(self, rev: Revision) -> list[Commit]:
        try:
            return utils.run_cmd(
                f"git -C {self.path} log --format=%H {rev}", capture_output=True
            ).split("\n")
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

    def is_ancestor(self, rev_old: Revision, rev_young: Revision) -> bool:
        rev_old = self.rev_to_commit(rev_old)
        rev_young = self.rev_to_commit(rev_young)

        process = subprocess.run(
            f"git -C {self.path} merge-base --is-ancestor {rev_old} {rev_young}".split(
                " "
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return process.returncode == 0

    def is_branch_point_ancestor_wrt_master(
        self, rev_old: Revision, rev_young: Revision
    ) -> bool:
        """
        In the following example, Young is not ancestor of Old but
        their respective best common ancestors wrt to main (i.e. the commit
        where they branched away from Main) are ancestors.

        Main
         | Young
         |/
         | Old
         |/

        Args:
            self:
            rev_old (Revision): rev_old
            rev_young (Revision): rev_young

        Returns:
            bool: True if their best common ancestors with main are ancestors.
        """
        rev_old = self.rev_to_commit(rev_old)
        rev_young = self.rev_to_commit(rev_young)
        rev_master = self.rev_to_commit("master")
        ca_young = self.get_best_common_ancestor(rev_master, rev_young)
        ca_old = self.get_best_common_ancestor(rev_master, rev_old)

        return self.is_ancestor(ca_old, ca_young)

    def on_same_branch_wrt_master(self, rev_a: Revision, rev_b: Revision) -> bool:
        rev_a = self.rev_to_commit(rev_a)
        rev_b = self.rev_to_commit(rev_b)
        rev_master = self.rev_to_commit("master")

        ca_a = self.get_best_common_ancestor(rev_a, rev_master)
        ca_b = self.get_best_common_ancestor(rev_b, rev_master)

        return ca_b == ca_a

    def get_unix_timestamp(self, rev: Revision) -> int:
        rev = self.rev_to_commit(rev)
        try:
            return int(
                utils.run_cmd(
                    f"git -C {self.path} log -1 --format=%at {rev}",
                    capture_output=True,
                )
            )
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

    def apply(self, patches: list[Path], check: bool = False) -> bool:
        patches = [patch.absolute() for patch in patches]
        git_patches = [
            str(patch) for patch in patches if not str(patch).endswith(".sh")
        ]
        sh_patches = [f"sh {patch}" for patch in patches if str(patch).endswith(".sh")]
        if check:
            git_cmd = f"git -C {self.path} apply --check".split(" ") + git_patches
            sh_patches = [patch_cmd + " --check" for patch_cmd in sh_patches]
        else:
            git_cmd = f"git -C {self.path} apply".split(" ") + git_patches

        returncode = 0
        for patch_cmd in [patch_cmd.split(" ") for patch_cmd in sh_patches]:
            logging.debug(patch_cmd)
            returncode += subprocess.run(
                patch_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode

        if len(git_patches) > 0:
            logging.debug(git_cmd)
            returncode += subprocess.run(
                git_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode

        return returncode == 0

    def next_bisection_commit(self, good: Revision, bad: Revision) -> Commit:
        request_str = (
            f"git -C {self.path} rev-list --bisect --first-parent {bad} ^{good}"
        )
        logging.debug(request_str)
        try:
            return utils.run_cmd(request_str, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

    def pull(self) -> None:
        """Pulls from the main branch of the repository.
        It will switch the repository to the main branch.
        It will also invalidate the caches of `rev_to_commit`
        , `get_best_common_ancestor` and `rev_to_tag`.

        Args:
            self:

        Returns:
            None:
        """
        self.rev_to_commit.cache_clear()
        self.get_best_common_ancestor.cache_clear()
        self.rev_to_tag.cache_clear()
        # Just in case...
        cmd0 = f"git -C {self.path} switch {self.main_branch}"
        cmd1 = f"git -C {self.path} pull"
        try:
            utils.run_cmd(cmd0)
            utils.run_cmd(cmd1)
        except subprocess.CalledProcessError as e:
            raise RepositoryException(e)

    @cache
    def rev_to_tag(self, rev: Revision) -> Optional[Commit]:
        request_str = f"git -C {self.path} describe --exact-match {rev}"
        logging.debug(request_str)
        output = subprocess.run(
            request_str.split(),
            capture_output=True,
        )
        stdout = output.stdout.decode("utf-8").strip()
        stderr = output.stderr.decode("utf-8").strip()
        if stderr.startswith("fatal:"):
            return None
        return stdout

    @cache
    def parent(self, rev: Revision) -> Commit:
        request_str = f"git -C {self.path} rev-parse {rev}^@"
        try:
            res = utils.run_cmd(request_str, capture_output=True)
        except subprocess.SubprocessError as e:
            raise RepositoryException(e)

        assert len(res.split("\n")) == 1
        return res

    def prune_worktree(self) -> None:
        prune_str = f"git -C {self.path} worktree prune"
        try:
            utils.run_cmd(prune_str, capture_output=True)
        except subprocess.SubprocessError as e:
            raise RepositoryException(e)

    def tags(self) -> list[Revision]:
        print_cmd = f"git -C {self.path} tag -l"
        try:
            res = utils.run_cmd(print_cmd, capture_output=True)
        except subprocess.SubprocessError as e:
            raise RepositoryException(e)
        return res.splitlines()


def get_llvm_repo(path_to_repo: Optional[Path] = None) -> Repo:
    if path_to_repo:
        return Repo(path_to_repo, "main")
    return Repo(DEFAULT_REPOS_DIR / "llvm-project", "main")


def get_gcc_repo(path_to_repo: Optional[Path] = None) -> Repo:
    if path_to_repo:
        return Repo(path_to_repo, "master")
    return Repo(DEFAULT_REPOS_DIR / "gcc", "master")


def get_gcc_releases(repo: Repo) -> list[Revision]:
    releases = []
    for r in repo.tags():
        if not r.startswith("releases/gcc-"):
            continue
        # We filter out older releases that we can't build
        should_skip = False
        for v in ("2", "3", "4", "5", "6"):
            if r.startswith(f"releases/gcc-{v}."):
                should_skip = True
                break
        if should_skip:
            continue
        releases.append(r)

    return sorted(
        releases, reverse=True, key=lambda x: int(x.split("-")[-1].replace(".", ""))
    )


def get_llvm_releases(repo: Repo) -> list[Revision]:
    releases = []
    for r in repo.tags():
        if not r.startswith("llvmorg-"):
            continue
        if "-rc" in r or "init" in r:
            continue
        # We filter out older releases that we can't build
        should_skip = False
        for v in ("1", "2", "3", "4"):
            if r.startswith(f"llvmorg-{v}."):
                should_skip = True
                break
        if should_skip:
            continue
        releases.append(r)

    return sorted(
        releases, reverse=True, key=lambda x: int(x.split("-")[-1].replace(".", ""))
    )


def get_releases(project: utils.CompilerProject, repo: Repo) -> list[Revision]:
    match project:
        case utils.CompilerProject.GCC:
            return get_gcc_releases(repo)
        case utils.CompilerProject.LLVM:
            return get_llvm_releases(repo)

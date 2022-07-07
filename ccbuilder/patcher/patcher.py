#!/usr/bin/env python3

"""

This code tries to figure out where a patch needs to be applied for a build to succeed. 
To achieve this, it heavily uses git and bisections and with that the terms 'good' and 'bad'.
However, 'good' does mean something different in different circumstances does not always indicate
a successful build.

The convention used for good and bad is taken from the normal use-case of `git bisect`:
    Something broke along the way and is now bad. Additionally, we know a commit, which worked,
    which is good.
So the bad commit is the newest w.r.t. the git history i.e. good is ancestor of bad.
And this is how 'good' and 'bad' are (should be) used in this file.

Other concepts are:
    introducer: The first commit, that makes a test fail.
    fixer: The first commit, that does not make a test fail, i.e. fixed it.
"""

from __future__ import annotations

import logging
import math
from enum import Enum
from pathlib import Path
from typing import Optional

from ccbuilder.builder.builder import (
    Builder,
    BuildException,
)
from ccbuilder.patcher.patchdatabase import PatchDB
from ccbuilder.utils.utils import CompilerReleases, CompilerProject
from ccbuilder.utils.repository import Repo, Commit, Revision


class PatchingResult(Enum):
    BuildsWithoutPatching = 1
    BuildsWithPatching = 2
    BuildFailed = 3


class Patcher:
    def __init__(self, prefix: Path, patchdb: PatchDB, builder: Builder, cores: int):
        self.prefix = prefix
        self.patchdb = patchdb
        self.cores = cores
        self.builder = builder

    def _build(
        self,
        project: CompilerProject,
        rev: Revision,
        additional_patches: list[Path] = [],
    ) -> None:
        self.builder.build(project, rev=rev, additional_patches=additional_patches)

    def _check_building_patches(
        self,
        project: CompilerProject,
        repo: Repo,
        rev: Revision,
        patches: list[Path],
    ) -> PatchingResult:
        if not self.patchdb.requires_all_these_patches(
            repo.rev_to_commit(rev), patches
        ):
            try:
                logging.info(f"Building {rev} without patches {patches}...")
                self._build(project, rev)
                return PatchingResult.BuildsWithoutPatching

            except BuildException as e:
                logging.info(f"Failed to build {rev} without patches {patches}: {e}")

            try:
                logging.info(f"Building {rev} WITH patches {patches}...")
                self._build(project, rev, additional_patches=patches)
                return PatchingResult.BuildsWithPatching

            except BuildException as e:
                logging.critical(
                    f"Failed to build {rev} with patches {patches}. Manual intervention needed. Exception: {e}"
                )
                self.patchdb.manual_intervention_required(project, rev)
                return PatchingResult.BuildFailed
        else:
            logging.info(f"Read form PatchDB: {rev} requires patches {patches}")
            return PatchingResult.BuildsWithPatching

    def _adjust_bisection_midpoint_after_failure(
        self,
        project: CompilerProject,
        repo: Repo,
        double_fail_counter: int,
        max_double_fail: int,
        bad: Revision,
        midpoint: Revision,
        good: Revision,
    ) -> Commit:
        """
        Move the midpoint either forwards or backwards (depending on the double_fail_counter)

        Returns:
            new midpoint (str)
        """
        if double_fail_counter >= max_double_fail:
            raise Exception(
                "Failed too many times in a row while bisecting. Aborting bisection..."
            )
        # TODO: More robust testing.
        # XXX: what's the purpose of this check? To switch between moving the
        # midpoint forwards and backwards?
        if double_fail_counter % 2 == 0:
            # Get size of range
            range_size = len(repo.direct_first_parent_path(midpoint, bad))

            # Move 10% towards the last bad
            step = max(int(0.9 * range_size), 1)
            midpoint = repo.rev_to_commit(f"{bad}~{step}")
        else:
            # Symmetric to case above
            range_size = len(repo.direct_first_parent_path(good, midpoint))
            step = max(int(0.2 * range_size), 1)
            midpoint = repo.rev_to_commit(f"{midpoint}~{step}")
        return midpoint

    def _bisection(
        self,
        good_rev: Commit,
        bad_rev: Commit,
        project: CompilerProject,
        repo: Repo,
        patches: list[Path],
        failure_is_good: bool = False,
        max_double_fail: int = 2,
    ) -> tuple[Commit, Commit]:

        good = good_rev
        bad = bad_rev

        # When building a particular commit and it fails to build
        # without the patches and with the patches, it is considered
        # a double fail.
        double_fail_counter = 0
        encountered_double_fail = False

        # Bisection
        midpoint: str = ""
        while True:
            if encountered_double_fail:
                midpoint = self._adjust_bisection_midpoint_after_failure(
                    project,
                    repo,
                    double_fail_counter,
                    max_double_fail,
                    bad,
                    midpoint,
                    good,
                )
                double_fail_counter += 1
                encountered_double_fail = False
            else:
                old_midpoint = midpoint
                midpoint = repo.next_bisection_commit(good=good, bad=bad)
                logging.info(f"Midpoint: {midpoint}")
                if midpoint == "" or midpoint == old_midpoint:
                    break

            patching_result = self._check_building_patches(
                project, repo, midpoint, patches
            )

            if patching_result is PatchingResult.BuildFailed:
                encountered_double_fail = True
            elif (
                patching_result is PatchingResult.BuildsWithoutPatching
            ) ^ failure_is_good:
                good = midpoint
            else:
                bad = midpoint

        return good, bad

    def _find_oldest_ancestor_not_needing_patches_and_oldest_patchable_from_releases(
        self,
        project: CompilerProject,
        repo: Repo,
        patchable_commit: Commit,
        potentially_human_readable_name: str,
        patches: list[Path],
    ) -> tuple[Optional[Commit], Optional[Commit]]:
        """Find two oldest common ancestors of patchable_commit and one of the releases:
            (1) that doesn't need the patch(es)
            (2) that is buildable only with the patch(es)

        Args:
            self:
            project (CompilerProject): Compiler project that is worked on
            repo (Repo): Repository of the `project`
            patchable_commit (Commit): Commit that is known to be broken without
                patches but is patchable with `patches`.
            potentially_human_readable_name (str): A different name for the commit
                for more human-friendly output.
            patches (list[Path]): Patches that fix `patchable_commit`.

        Returns:
            tuple[Optional[Commit], Optional[Commit]]: Common ancestor (w.r.t. releases) that needs no
                patch and the oldest patchable commit found.
        """

        # For now, just assume this is sorted in descending release-recency
        # Using commit dates doesn't really work
        release_versions = ["trunk"] + CompilerReleases[project]
        release_versions.reverse()

        tested_ancestors: list[Commit] = []
        no_patch_common_ancestor = None
        oldest_patchable_ancestor = None
        for old_version in release_versions:

            logging.info(f"Testing for {old_version}")
            if not repo.is_branch_point_ancestor_wrt_master(
                old_version, patchable_commit
            ):
                if oldest_patchable_ancestor:
                    # XXX: Would something like "All older releases require
                    # patching" make more sense as a warning?
                    logging.warning(
                        f"Found only older releases requiring the patch(es)!"
                    )
                    break
                raise Exception(
                    "No buildable-version was found before patchable_commit!"
                )

            common_ancestor = repo.get_best_common_ancestor(
                old_version, patchable_commit
            )
            # TODO: Check that common_ancestor is ancestor of master.
            #       Otherwise we get stuck in a branch
            #       Is this actually a problem?
            if common_ancestor in tested_ancestors:
                logging.info(
                    f"Common ancestor of {old_version} and {potentially_human_readable_name} was already tested. Proceeding..."
                )
                continue

            # Building of releases
            patching_result = self._check_building_patches(
                project, repo, common_ancestor, patches
            )

            if patching_result is PatchingResult.BuildsWithoutPatching:
                no_patch_common_ancestor = common_ancestor
                break

            if patching_result is PatchingResult.BuildsWithPatching:
                if not oldest_patchable_ancestor:  # None have been found
                    oldest_patchable_ancestor = common_ancestor
            else:
                tested_ancestors.append(common_ancestor)

        return no_patch_common_ancestor, oldest_patchable_ancestor

    def find_ranges(
        self,
        project: CompilerProject,
        repo: Repo,
        patchable_commit: Commit,
        patches: list[Path],
    ) -> None:
        """Given a compiler, a revision of the compiler that normally does not build
        and the set of patches needed to fix this particular revision,
        find the continuous region on the commit history which requires all these
        patches to build.

        We assume that such region is continuous. That means in case there's a gap
        which does not need the patch, we may end up including the whole gap into the
        region or do not find the region after the gap (w.r.t. the starting commit)
        at all.

        Args:
            project (CompilerProject): The compiler project which has the problematic commit.
            repo (Repo): Repository corresponding to `project`.
            patchable_commit (Commit): The problematic commit which is buildable with `patches`.
            patches (list[Path]): Patches required to build `patchable_commit`.


        Returns:
            None:
        """
        introducer = ""

        potentially_human_readable_name = patchable_commit
        patchable_commit = repo.rev_to_commit(patchable_commit)
        patches = [patch.absolute() for patch in patches]
        for patch in patches:
            if not Path(patch).exists():
                logging.critical(f"Patch {patch} doesn't exist. Aborting...")
                raise Exception(f"Patch {patch} doesn't exist. Aborting...")
        (
            no_patch_common_ancestor,
            oldest_patchable_ancestor,
        ) = self._find_oldest_ancestor_not_needing_patches_and_oldest_patchable_from_releases(
            project,
            repo,
            patchable_commit,
            potentially_human_readable_name,
            patches,
        )

        # Possible cases
        # no_patch_common_ancestor was found AND oldest_patchable_ancestor was found
        #   - This only happens if a bug was re-introduced or the patch just so happens to fix
        #     another intermediate bug.
        #     For simplicity we assume that BOTH were the ONLY ones found so we are fine
        #
        # ONLY no_patch_common_ancestor was found (hopefully the common case)
        #   - Proceed with bisection of introducer commit
        #
        # ONLY oldest_patchable_ancestor was found
        #   - Find fixer commits from there (could do something if no_patch_common_ancestor exists)

        if no_patch_common_ancestor:
            # Find introducer commit
            _, introducer = self._bisection(
                no_patch_common_ancestor,
                patchable_commit,
                project,
                repo,
                patches,
            )

            # Insert from introducer to and with patchable_commit as requiring patching
            # This is of course not the complete range but will help when bisecting
            rev_range = f"{introducer}~..{patchable_commit}"
            commit_list = repo.rev_to_commit_list(rev_range)
            for patch in patches:
                self.patchdb.save(patch, commit_list, repo)

            self.find_fixer_from_introducer_to_releases(
                introducer=introducer,
                project=project,
                repo=repo,
                patches=patches,
            )

        elif oldest_patchable_ancestor:
            self.find_fixer_from_introducer_to_releases(
                introducer=oldest_patchable_ancestor,
                project=project,
                repo=repo,
                patches=patches,
            )

    def find_fixer_from_introducer_to_releases(
        self,
        introducer: Commit,
        project: CompilerProject,
        repo: Repo,
        patches: list[Path],
    ) -> None:
        logging.info(f"Starting bisection of fixer commits from {introducer}...")

        # Find reachable releases
        reachable_releases = [
            repo.rev_to_commit(release)
            for release in CompilerReleases[project]
            if repo.is_ancestor(introducer, release)
        ]

        last_needing_patch_list: list[str] = []
        for release in reachable_releases:
            logging.info(f"Searching fixer for release {release}")

            patching_result = self._check_building_patches(
                project, repo, release, patches
            )

            # Check if any of already found fixers is ancestor of release
            # As we assume that a fixer at a given point fixes all its children, this is fine.
            logging.info(f"Checking for known fixers...")
            if (
                len(last_needing_patch_list) > 0
                and patching_result.BuildsWithoutPatching
                and any(
                    [
                        repo.is_ancestor(fixer, release)
                        for fixer in last_needing_patch_list
                    ]
                )
            ):
                logging.info(f"Already known fixer. No additional searching required")
                continue

            if patching_result is PatchingResult.BuildFailed:
                continue

            elif patching_result is PatchingResult.BuildsWithPatching:
                # release only builds with patch, everything to release is to be included
                commits = repo.rev_to_commit_list(f"{introducer}~1..{release}")
                for patch in patches:
                    self.patchdb.save(patch, commits, repo)
                continue

            elif patching_result is PatchingResult.BuildsWithoutPatching:
                # Range A..B is includes B, thus we want B to be the last good one
                # as good requires the patch
                last_needing_patch, _ = self._bisection(
                    introducer,
                    release,
                    project,
                    repo,
                    patches,
                    failure_is_good=True,
                )

                last_needing_patch_list.append(last_needing_patch)
                range_needing_patching = repo.rev_to_range_needing_patch(
                    introducer, last_needing_patch
                )
                for patch in patches:
                    self.patchdb.save(
                        patch,
                        range_needing_patching,
                        repo,
                    )

        logging.info("Done finding fixers")

    def bisect_build(
        self,
        good: Commit,
        bad: Commit,
        project: CompilerProject,
        repo: Repo,
        failure_is_good: bool = False,
    ) -> tuple[Commit, Commit]:
        """Bisect w.r.t. building and not building.

        Args:
            good (Commit): Commit that is closer to the head of the branch than `bad`.
            bad (Commit): Commit that is further away from the head of the branch than `good`.
            project (CompilerConfig): Project being patched.
            repo (Repo): Repository for `project`.
            failure_is_good (bool): If failing to build is expected to happen to the `good` commit.

        Returns:
            tuple[Commit, Commit]: The two commits around the bisection point.
        """

        midpoint = ""

        while True:
            old_midpoint = midpoint
            midpoint = repo.next_bisection_commit(good=good, bad=bad)
            logging.info(f"Midpoint: {midpoint}")
            if midpoint == "" or midpoint == old_midpoint:
                break

            # ==================== BUILDING ====================
            try:
                logging.info(f"Building midpoint {midpoint}...")
                self._build(project, midpoint)
                if failure_is_good:
                    bad = midpoint
                else:
                    good = midpoint
                continue

            except BuildException as e:
                logging.info(f"Failed to build {midpoint}: {e}")
                if failure_is_good:
                    good = midpoint
                else:
                    bad = midpoint
                continue

        return (good, bad)

    def find_introducer(
        self, project: CompilerProject, repo: Repo, broken_rev: Revision
    ) -> Commit:
        """Given a broken commit, find the commit that introduced the build failure.

        Args:
            self:
            project (CompilerProject):
            repo (Repo): Repository for `project`
            broken_rev (Revision): Revision that is broken

        Returns:
            Commit: introducer commit hash
        """
        logging.info(f"Looking for introducer commit starting at {broken_rev}")

        oldest_possible_commit = repo.get_best_common_ancestor(
            CompilerReleases[project][-1], "main"
        )

        # === Introducer
        # ====== Search Phase

        exp = 0

        hit_upper_bound = False
        current_commit = broken_rev
        while True:
            prev_commit = current_commit
            current_commit = repo.rev_to_commit(broken_rev + f"~{2**exp + 10}")
            is_ancestor = repo.is_ancestor(oldest_possible_commit, current_commit)
            if hit_upper_bound:
                msg = (
                    f"Couldn't find buildable ancestor for broken revision {broken_rev}"
                )
                logging.critical(msg)
                raise Exception(msg)

            if not is_ancestor and not hit_upper_bound:
                current_commit = oldest_possible_commit
                hit_upper_bound = True

            try:
                logging.info(f"Building {current_commit} in search of buildable one")
                self._build(project, current_commit)
                break
            except BuildException as e:
                exp += 1
                logging.info(
                    f"Failed to build {current_commit}. Increasing exponent to {exp}: {e}"
                )

        # ====== Bisection
        msg = f"Staring bisection between {current_commit} and {prev_commit}, should take at most around {math.log(max(2**exp, 11) - 2**min(exp-1, 0), 2)} steps"
        logging.info(msg)
        print(msg)
        _, introducer = self.bisect_build(
            good=current_commit,
            bad=prev_commit,
            project=project,
            repo=repo,
            failure_is_good=False,
        )
        msg = f"Found introducer {introducer}"
        logging.info(msg)
        print(msg)
        return introducer

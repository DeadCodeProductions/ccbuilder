from dataclasses import dataclass
from diopter.bisector import BisectionCallback
from diopter.repository import Commit, Repo
from diopter.compiler import CompilerProject

from ccbuilder.compilerstore import CompilerStore


class CachedBisectionCallback(BisectionCallback):
    """A bisection callback that steers the bisection to use already built compilers."""

    def __init__(
        self, cstore: CompilerStore, project: CompilerProject, repo: Repo
    ) -> None:
        self.cstore = cstore
        self.project = project
        self.repo = repo

    def shift_tested_commit(
        self, commit: Commit, lower_bound: Commit, upper_bound: Commit
    ) -> Commit:
        """Shift the tested commit to a commit that has been built."""
        nearest_commit = self.cstore.get_closest_built_compiler_in_range(
            self.project, commit, lower_bound, upper_bound, self.repo
        )
        if nearest_commit is None:
            return commit
        return self.repo.rev_to_commit(nearest_commit.commit)

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import atexit
import json

from diopter.repository import Repo, Commit
from diopter.compiler import CompilerProject, CompilerExe


@dataclass(frozen=True, slots=True)
class BuiltCompilerInfo:
    """Information about a compiler built by ccbuilder.

    Attributes:
        project (CompilerProject):
            GCC or LLVM
        prefix (Path):
            The prefix where the compiler was installed. This should
            be subdirectory of the ccbuilder compiler store
        commit (Commit):
            The (git) commit of the compiler that was built
    """

    project: CompilerProject
    prefix: Path
    commit: Commit

    def get_compiler(self, cpp: bool = False) -> CompilerExe:
        """Get the compiler executable for this compiler.
        Args:
            cpp (bool):
                Whether to get the C++ or C frontend
        Returns:
            CompilerExe: The compiler executable
        """
        match self.project:
            case CompilerProject.GCC:
                driver = "g++" if cpp else "gcc"
            case CompilerProject.LLVM:
                driver = "clang++" if cpp else "clang"
        return CompilerExe(self.project, self.prefix / "bin" / driver, self.commit)


@dataclass(kw_only=True)
class CompilerStore:
    """A database of compilers built by ccbuilder.

    Appart from the built compilers, the database also stores
    the set of commits that ccbuilder faild to build.
    """

    def __init__(self, db_path: Path) -> None:
        """
        Args:
            db_path (Path):
                The path to the database file. If the file
                does not exist, it will be created.
        """
        self.con = sqlite3.connect(db_path, timeout=60)
        atexit.register(self.con.close)
        with self.con:
            cur = self.con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS compilers(
                    project TEXT,
                    commit_ TEXT,
                    prefix TEXT,
                    UNIQUE(project, commit_)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS failed_compiler_builts(
                    project TEXT,
                    commit_ TEXT,
                    UNIQUE(project, commit_)
                )
                """
            )

    def add_compiler(self, compiler: BuiltCompilerInfo) -> None:
        """Add a compiler to the database.
        Args:
            compiler (BuiltCompilerInfo):
                The compiler to add.
        """
        self.add_compilers([compiler])

    def add_compilers(self, compilers: list[BuiltCompilerInfo]) -> None:
        """Add multiple compilers to the database.
        Args:
            compilers (list[BuiltCompilerInfo]):
                The compilers to add.
        """
        new_compilers = []
        remove_from_failed = []
        for compiler in compilers:
            already_stored = self.get_built_compiler(compiler.project, compiler.commit)
            if already_stored is not None:
                assert already_stored == compiler
            else:
                new_compilers.append(
                    (compiler.project.name, compiler.commit, str(compiler.prefix))
                )
            if self.has_previously_failed_to_build(compiler.project, compiler.commit):
                remove_from_failed.append((compiler.project.name, compiler.commit))

        with self.con:
            self.con.executemany(
                "INSERT INTO compilers VALUES (?, ?, ?)",
                new_compilers,
            )
            self.con.executemany(
                "DELETE FROM failed_compiler_builts WHERE project=? AND commit_=?",
                remove_from_failed,
            )

    def add_failed_to_build_compiler(
        self, project: CompilerProject, commit: Commit
    ) -> None:
        """Record that ccbuilder failed to build a compiler.
        Args:
            project (CompilerProject):
                The compiler project (GCC or LLVM)
            commit (Commit):
                The commit of the compiler that failed to build
        """
        if self.has_previously_failed_to_build(project, commit):
            return
        with self.con:
            self.con.execute(
                "INSERT INTO failed_compiler_builts VALUES (?, ?)",
                (project.name, commit),
            )

    def has_previously_failed_to_build(
        self, project: CompilerProject, commit: Commit
    ) -> bool:
        """Check if ccbuilder has previously failed to build a compiler.
        Args:
            project (CompilerProject):
                The compiler project (GCC or LLVM)
            commit (Commit):
                The commit of the compiler to check.
        Returns:
            bool:
                True if ccbuilder has previously failed to build the compiler.
        """
        result = self.con.execute(
            "SELECT * FROM failed_compiler_builts WHERE project=? AND commit_=?",
            (project.name, commit),
        ).fetchone()
        return result is not None

    def failed_to_build_compilers(self) -> list[tuple[CompilerProject, Commit]]:
        """Get the list of compilers that ccbuilder has previously failed to build.
        Returns:
            list[tuple[CompilerProject, Commit]]:
                A list of (compiler, commit) pairs that ccbuilder has previously failed to build.
        """
        return [
            (p, Commit(c))
            for p, c in self.con.execute(
                "SELECT project, commit_ FROM failed_compiler_builts"
            )
        ]

    def remove_from_previously_failed_to_build(
        self, project: CompilerProject, commit: Commit
    ) -> None:
        """Remove a commit from the list of that ccbuilder has previously failed to build.
        Args:
            project (CompilerProject):
                The compiler project (GCC or LLVM)
            commit (Commit):
                The commit to remove.
        """
        with self.con:
            self.con.execute(
                "DELETE FROM failed_compiler_builts WHERE project=? AND commit_=?",
                (project.name, commit),
            )

    def clear_previously_failed_to_build(self) -> None:
        """Delete all the commits from the list of that ccbuilder has previously failed to build."""
        with self.con:
            self.con.execute("DELETE FROM failed_compiler_builts")

    def remove_compiler(self, project: CompilerProject, commit: Commit) -> None:
        """Remove a built compiler from the database.

        If the given compiler is not in the database, nothing happens.

        Args:
            project (CompilerProject):
                The compiler project (GCC or LLVM)
            commit (Commit):
                The commit of the compiler to remove.
        """

        with self.con:
            self.con.execute(
                "DELETE FROM compilers WHERE project=? AND commit_=?",
                (project.name, commit),
            )

    def get_built_compiler(
        self, project: CompilerProject, commit: Commit
    ) -> BuiltCompilerInfo | None:
        """Retrieve a built compiler info from the database.
        Args:
            project (CompilerProject):
                The compiler project (GCC or LLVM)
            commit (Commit):
                The commit of the compiler to retrieve.
        Returns:
            BuiltCompilerInfo | None:
                The built compiler info if it is in the database, None otherwise.
        """
        result = self.con.execute(
            "SELECT * FROM compilers WHERE project=? AND commit_=?",
            (project.name, commit),
        ).fetchone()
        if result is None:
            return None
        bci = BuiltCompilerInfo(project=project, prefix=Path(result[2]), commit=commit)
        if not (bci.prefix / "DONE").exists():
            print(
                f"WARNING: {bci.prefix} does not exist but was found in the compiler database, removing it."
            )
            self.remove_compiler(project, commit)
            return None

        return bci

    def built_commits(self, project: CompilerProject) -> list[Commit]:
        """Get the list of built commits for the given compiler.
        Args:
            project (CompilerProject):
                The compiler project (GCC or LLVM)
        Returns:
            list[Commit]:
                The list of built commits for the given compiler.
        """
        return [
            Commit(c)
            for c, in self.con.execute(
                "SELECT commit_ FROM compilers WHERE project=?", (project.name,)
            )
        ]

    def get_closest_built_compiler_in_range(
        self,
        project: CompilerProject,
        revision: Commit,
        lower_bound: Commit,
        upper_bound: Commit,
        repo: Repo,
    ) -> BuiltCompilerInfo | None:
        """Given a commit find the closest built commit in the `lower_bound` and `upper_bound` range.
        Args:
            project (CompilerProject):
                The compiler project (GCC or LLVM)
            revision (Commit):
                The commit to find the closest built commit for.
            lower_bound (Commit):
                The lower bound of the range to search in.
            upper_bound (Commit):
                The upper bound of the range to search in.
            repo (Repo):
                The repository corresponding to `project`.
        Returns:
            BuiltCompilerInfo | None:
                The closest built compiler info in the range, or None if there is none.
        """
        revision = repo.rev_to_commit(revision)
        lower_bound = repo.rev_to_commit(lower_bound)
        upper_bound = repo.rev_to_commit(upper_bound)
        assert repo.is_ancestor(revision, upper_bound)

        if built := self.get_built_compiler(project, revision):
            return built
        built_commits = set(self.built_commits(project))

        # [(lower_bound),..., (revision),..., (upper_bound)]
        commits = list(
            reversed(
                list(
                    c
                    for c in repo.direct_first_parent_path(lower_bound, upper_bound)
                    if (c in built_commits or c == revision)
                    and c != lower_bound
                    and c != upper_bound
                )
            )
        )

        if revision not in commits:
            # We must be testing a lower bound merge base
            assert repo.is_ancestor(
                revision, lower_bound
            ), f"{revision} not in {lower_bound}...{upper_bound}"
            if commits:
                return self.get_built_compiler(project, commits[0])
            else:
                return None

        if len(commits) == 1:
            return None

        for i, c in enumerate(commits):
            if c == revision:
                break

        if i == 0:
            target_commit = commits[1]
        elif i == len(commits) - 1:
            target_commit = commits[-2]
        else:
            # Next or previous? Is there a better way of selecting?
            target_commit = commits[i + 1]
        return self.get_built_compiler(project, target_commit)


def default_store_file(store_prefix: Path) -> Path:
    """Get the default compiler store file path.

    The default compiler store file path is `store_prefix/compiler_store/compilerstore.db`.
    If `store_prefix/compiler_store` does not exist, it is created.

    Args:
        store_prefix (Path):
            Where all the built compilers are stored.
    Returns:
        Path:
            The default compiler store (database) file path.
    """
    return store_prefix / "compiler_store" / "compilerstore.db"


def scan_directory_for_compilers(path: Path) -> list[BuiltCompilerInfo]:
    """Scan a directory for built compilers.
    Args:
        path (Path):
            The path to scan.
    Returns:
        list[BuiltCompilerInfo]:
            The list of built compilers found in the directory.
    """
    assert path.is_dir()
    built_compilers = []
    for p in path.iterdir():
        if p.is_symlink():
            continue
        if not p.is_dir():
            continue
        if not (p / "DONE").exists():
            continue
        if p.name.startswith("gcc-"):
            project = CompilerProject.GCC
        elif p.name.startswith("clang-"):
            project = CompilerProject.LLVM
        else:
            continue
        commit = Commit(p.name.split("-")[1])
        prefix = p.resolve(True)
        built_compilers.append(BuiltCompilerInfo(project, prefix, commit))
    return built_compilers


def scan_directory_and_populate_store(path: Path, store: CompilerStore) -> None:
    """Scan a directory for built compilers and add them to the store.
    Args:
        path (Path):
            The path to scan.
        store (CompilerStore):
            The compiler store to add the compilers to.
    """
    store.add_compilers(scan_directory_for_compilers(path))


def load_compiler_store(path: Path) -> CompilerStore:
    """Load a compiler store from a file.
    Args:
        path (Path):
            The path to the compiler store file.
    Returns:
        CompilerStore:
            The loaded compiler store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return CompilerStore(path)

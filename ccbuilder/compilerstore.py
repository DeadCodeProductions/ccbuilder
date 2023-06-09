from dataclasses import dataclass
from pathlib import Path
import sqlite3
import atexit
import json

from diopter.repository import Repo, Commit
from diopter.compiler import CompilerProject, CompilerExe


@dataclass(frozen=True, slots=True)
class BuiltCompilerInfo:
    project: CompilerProject
    prefix: Path
    commit: Commit

    def get_compiler(self, cpp: bool = False) -> CompilerExe:
        match self.project:
            case CompilerProject.GCC:
                driver = "g++" if cpp else "gcc"
            case CompilerProject.LLVM:
                driver = "clang++" if cpp else "clang"
        return CompilerExe(self.project, self.prefix / "bin" / driver, self.commit)


@dataclass(kw_only=True)
class CompilerStore:
    def __init__(self, db_path: Path) -> None:
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
        self.add_compilers([compiler])

    def add_compilers(self, compilers: list[BuiltCompilerInfo]) -> None:
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
                remove_from_failed.append((compiler.project, compiler.commit))

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
        with self.con:
            self.con.execute(
                "INSERT INTO failed_compiler_builts VALUES (?, ?)",
                (project.name, commit),
            )

    def has_previously_failed_to_build(
        self, project: CompilerProject, commit: Commit
    ) -> bool:
        result = self.con.execute(
            "SELECT * FROM failed_compiler_builts WHERE project=? AND commit_=?",
            (project.name, commit),
        ).fetchone()
        return result is not None

    def remove_from_previously_failed_to_build(
        self, project: CompilerProject, commit: Commit
    ) -> None:
        with self.con:
            self.con.execute(
                "DELETE FROM failed_compiler_builts WHERE project=? AND commit_=?",
                (project.name, commit),
            )

    def remove_compiler(self, project: CompilerProject, commit: Commit) -> None:
        with self.con:
            self.con.execute(
                "DELETE FROM compilers WHERE project=? AND commit_=?",
                (project.name, commit),
            )

    def get_built_compiler(
        self, project: CompilerProject, commit: Commit
    ) -> BuiltCompilerInfo | None:
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
        revision = repo.rev_to_commit(revision)
        lower_bound = repo.rev_to_commit(lower_bound)
        upper_bound = repo.rev_to_commit(upper_bound)
        if built := self.get_built_compiler(project, revision):
            return built
        built_commits = set(self.built_commits(project))

        # [(lower_bound),..., (revision),..., (upper_bound)]
        commits = list(
            reversed(
                list(
                    c
                    for c in repo.direct_first_parent_path(lower_bound, upper_bound)
                    if c in built_commits or c == revision
                )
            )
        )
        assert revision in commits, f"{revision} not in {lower_bound}...{upper_bound}"

        if lower_bound == commits[0]:
            commits = commits[1:]
        if upper_bound == commits[-1]:
            commits = commits[:-1]

        # Temporary asserts, to make sure that the algorithm is correct
        assert lower_bound not in commits
        assert upper_bound not in commits

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


def default_store_file(cache_prefix: Path) -> Path:
    return cache_prefix / "compiler_store" / "compilerstore.db"


def scan_directory_for_compilers(path: Path) -> list[BuiltCompilerInfo]:
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
    store.add_compilers(scan_directory_for_compilers(path))


def load_compiler_store(path: Path) -> CompilerStore:
    if path.exists():
        if path.stat().st_mtime < path.parent.parent.stat().st_mtime:
            print(
                f"WARNING: Compiler store {path} is older than its parent "
                f"directory {path.parent.parent}, run ccbuilber cache scan"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    return CompilerStore(path)

import logging
import json
import multiprocessing
import os
import sys
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Optional, TextIO

from diopter.repository import Repo, Revision, Commit
from diopter.compiler import CompilerProject

from ccbuilder.patcher.patchdatabase import PatchDB
from ccbuilder.compilerstore import CompilerStore, BuiltCompilerInfo
from ccbuilder.utils.utils import (
    run_cmd_to_logfile,
    select_repo,
)


class BuildException(Exception):
    pass


class DuplicateBuildException(Exception):
    pass


def _worker_alive(worker_indicator: Path) -> bool:
    if worker_indicator.exists():
        with open(worker_indicator, "r") as f:
            worker_pid = int(f.read())
        try:
            # Signal to ~check if a process is alive
            # from man 1 kill
            # > If signal is 0, then no actual signal is sent, but error checking is still performed.
            os.kill(worker_pid, 0)
            return True
        except OSError:
            return False
    return False


@dataclass
class BuildContext:
    """Creates a temporary directory for a build, creates the install prefix
    and ensures that a compiler is not built twice.
    """

    install_prefix: Path
    success_indicator: Path
    project: CompilerProject
    commit_to_build: Commit
    logdir: Optional[Path]

    def __enter__(self) -> tuple[Path, TextIO]:
        self.build_dir = tempfile.mkdtemp()
        os.makedirs(self.install_prefix, exist_ok=True)
        worker_pid_file = self.install_prefix / "WORKER_PID"

        if worker_pid_file.exists():
            if _worker_alive(worker_pid_file):
                raise DuplicateBuildException(
                    "The compiler is either being built by another process "
                    "or the cache in a bad state (run ccbuilder cache clean to fix)."
                )

        # Write worker PID
        with open(self.install_prefix / "WORKER_PID", "w") as f:
            f.write(str(os.getpid()))

        self.starting_cwd = os.getcwd()
        os.chdir(self.build_dir)

        if self.logdir:
            # Build log file
            current_time = time.strftime("%Y%m%d-%H%M%S")
            name = self.project.to_string()
            build_log_path = (
                self.logdir / f"{current_time}-{name}-{self.commit_to_build}.log"
            )
            self.build_log = open(build_log_path, "a")
            logging.info(f"Build log at {build_log_path}")
            return (Path(self.build_dir), self.build_log)
        else:
            return (Path(self.build_dir), sys.stderr)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_traceback: Optional[TracebackType],
    ) -> None:
        if self.logdir:
            self.build_log.close()
        shutil.rmtree(self.build_dir)
        os.chdir(self.starting_cwd)
        os.remove(self.install_prefix / "WORKER_PID")

        # Build was not successful
        if not self.success_indicator.exists():
            # remove cache entry
            shutil.rmtree(self.install_prefix)


def apply_patches(git_dir: Path, patches: list[Path]) -> bool:
    """Applies the given `patches` to `git_dir` in the order defined by `patches`.

    Args:
        git_dir (Path): Path to git repository to apply the patches to.
        patches (list[Path]): List of patches to apply.

    Returns:
        bool: True if it was possible to apply all the patches.
    """
    patches = [patch.absolute() for patch in patches]
    git_patches = [str(patch) for patch in patches if not str(patch).endswith(".sh")]
    sh_patches = [f"sh {patch}" for patch in patches if str(patch).endswith(".sh")]
    git_cmd = f"git -C {git_dir} apply".split(" ") + git_patches

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


def patch_if_necessary(
    commit_to_patch: Commit,
    patchdb: PatchDB,
    working_dir: Path,
    additional_patches: list[Path] = [],
) -> None:
    """Looks up the known required patches for `commit_to_patch` in `patchdb`
    and applies them, if necessary.
    May raise a `BuildException` if the patches could not be applied.

    Args:
        commit_to_patch (Commit): The commit to look up the patches
        patchdb (PatchDB): PatchDB
        working_dir (Path): Path to git repository which has `commit_to_patch` checked out.
        additional_patches (list[Path]): Additional patches to apply *after* the ones found in `patchdb`.

    Returns:
        None:
    """
    patches = patchdb.required_patches(commit_to_patch) + additional_patches
    if patches:
        if not apply_patches(working_dir, patches):
            raise BuildException(f"Could not apply patches: {patches}")


def llvm_build_and_install(
    install_prefix: Path, jobs: int, log_file: TextIO, configure_flags: str | None
) -> None:
    """Build and install LLVM. Must only be called in a `BuildContext`.
    May raise a `CalledProcessError`.

    Args:
        install_prefix (Path): Install prefix for the build (in the cache).
        jobs (int): Amount of jobs to build with
        log_file (TextIO): File to log the build process to.
        configure_flags (str | None): additional flags to pass to the configure script or cmake.

    Returns:
        None:
    """
    os.chdir("build")
    logging.debug("LLVM: Starting cmake")
    cmake_cmd = (
        "cmake ../llvm -G Ninja -DCMAKE_BUILD_TYPE=Release "
        "-DLLVM_ENABLE_PROJECTS=clang -DLLVM_INCLUDE_BENCHMARKS=OFF "
        "-DLLVM_INCLUDE_TESTS=OFF -DLLVM_USE_NEWPM=ON -DLLVM_TARGETS_TO_BUILD=X86 "
        f"-DCMAKE_INSTALL_PREFIX={install_prefix} -DLLVM_LINK_LLVM_DYLIB=ON -DLLVM_BUILD_LLVM_DYLIB=ON"
    )
    if configure_flags:
        cmake_cmd += " " + configure_flags

    run_cmd_to_logfile(
        cmake_cmd, additional_env={"CC": "clang", "CXX": "clang++"}, log_file=log_file
    )

    logging.debug("LLVM: Starting to build...")
    run_cmd_to_logfile(f"ninja -j {jobs} install", log_file=log_file)


def gcc_build_and_install(
    install_prefix: Path, jobs: int, log_file: TextIO, configure_flags: str | None
) -> None:
    """Build and install GCC. Must only be called in a `BuildContext`.
    May raise a `CalledProcessError`.

    Args:
        install_prefix (Path): Install prefix for the build (in the cache).
        jobs (int): Amount of jobs to build with
        log_file (TextIO): File to log the build process to.
        configure_flags (str | None): additional flags to pass to the configure script or cmake.

    Returns:
        None:
    """
    pre_cmd = "./contrib/download_prerequisites"
    logging.debug("GCC: Starting download_prerequisites")
    run_cmd_to_logfile(pre_cmd, log_file=log_file)

    os.chdir("build")
    logging.debug("GCC: Starting configure")
    configure_cmd = (
        "../configure --disable-multilib --disable-bootstrap"
        f" --enable-languages=c,c++ --prefix={install_prefix}"
    )
    if configure_flags:
        configure_cmd += " " + configure_flags
    run_cmd_to_logfile(configure_cmd, log_file=log_file)

    logging.debug("GCC: Starting to build...")
    run_cmd_to_logfile(f"make -j {jobs}", log_file=log_file)
    run_cmd_to_logfile("make install-strip", log_file=log_file)


def _run_build_and_install(
    project: CompilerProject,
    install_path: Path,
    cores: int,
    log_file: TextIO,
    configure_flags: str | None,
) -> Path:
    os.makedirs("build")
    try:
        match project:
            case CompilerProject.GCC:
                gcc_build_and_install(install_path, cores, log_file, configure_flags)
            case CompilerProject.LLVM:
                llvm_build_and_install(install_path, cores, log_file, configure_flags)
    except subprocess.CalledProcessError as e:
        raise BuildException(f"Build failed: {e}")
    return install_path


def get_install_path(
    cache_prefix: Path, project: CompilerProject, commit: Commit
) -> Path:
    """Get the install path for a given project and commit
    combination.
    `cache_prefix` / projectname-commit

    Args:
        cache_prefix (Path): cache_prefix
        project (CompilerProject): project
        commit (Commit): commit

    Returns:
        Path: cache_prefix / projectname-commit
    """
    match project:
        case CompilerProject.LLVM:
            install_name_prefix = "clang"
        case CompilerProject.GCC:
            install_name_prefix = "gcc"
    return cache_prefix / f"{install_name_prefix}-{commit}"


def build_and_install_compiler(
    project: CompilerProject,
    rev: Revision | Commit,
    cache_prefix: Path,
    llvm_repo: Repo,
    gcc_repo: Repo,
    patchdb: PatchDB,
    jobs: Optional[int] = None,
    additional_patches: Optional[list[Path]] = None,
    configure_flags: str | None = None,
    logdir: Optional[Path] = None,
) -> Path:
    """Build and install a compiler specified via `project` and `rev`.
    Will install to `cache_prefix`/projectname-commit.


    Args:
        project (CompilerProject): LLVM or GCC.
        rev (Revision): rev
        cache_prefix (Path): cache_prefix
        llvm_repo (Repo): LLVM repository
        gcc_repo (Repo): GCC repository
        patchdb (PatchDB): The PatchDB to use.
        jobs (Optional[int]): Amount of jobs
        additional_patches (Optional[list[Path]]): Additional patches to apply.
        configure_flags (str | None): additional flags to pass to the configure script or cmake.
        logdir (Optional[Path]): Path to where the build logs are saved.

    Returns:
        Path: `cache_prefix`/projectname-commit
    """

    repo = select_repo(project, llvm_repo, gcc_repo)
    commit = repo.rev_to_commit(rev)
    install_prefix = get_install_path(cache_prefix, project, commit)
    success_indicator = install_prefix / "DONE"

    if logdir:
        logdir.mkdir(exist_ok=True, parents=True)
    try:
        with BuildContext(
            install_prefix, success_indicator, project, commit, logdir
        ) as (
            tmpdir,
            build_log,
        ):
            run_cmd_to_logfile(
                f"git -C {str(repo.path)} worktree" f" add {tmpdir} {commit} -f",
                log_file=build_log,
            )
            patch_if_necessary(
                commit,
                patchdb,
                Path(tmpdir),
                additional_patches if additional_patches else [],
            )
            res = _run_build_and_install(
                project,
                install_prefix,
                jobs if jobs else multiprocessing.cpu_count(),
                build_log,
                configure_flags,
            )
            success_indicator.touch()
            with open(success_indicator, "w") as f:
                json.dump(
                    {
                        "revision": rev,
                        "configure": configure_flags if configure_flags else "",
                    },
                    f,
                )
    finally:
        repo.prune_worktree()
    return res


class Builder:
    def __init__(
        self,
        cache_prefix: Path,
        gcc_repo: Repo,
        llvm_repo: Repo,
        cstore: CompilerStore,
        patchdb: Optional[PatchDB] = None,
        jobs: Optional[int] = None,
        logdir: Optional[Path] = None,
    ):
        self.cache_prefix = cache_prefix
        self.gcc_repo = gcc_repo
        self.llvm_repo = llvm_repo
        self.cstore = cstore

        pdb = patchdb if patchdb else PatchDB()
        self.patchdb = pdb
        self.jobs = jobs if jobs else multiprocessing.cpu_count()
        self.logdir = logdir

    def build(
        self,
        project: CompilerProject,
        rev: Revision | Commit,
        additional_patches: Optional[list[Path]] = None,
        configure_flags: str | None = None,
        jobs: Optional[int] = None,
        force: bool = False,
    ) -> BuiltCompilerInfo:
        """Build the specified compiler and return the path to the installation location.
        If the specified compiler has already been built, `build` will return the cached path.

        Args:
            self:
            project (CompilerProject): Project to build.
            rev (str): Revision of the project to build.
            additional_patches (Optional[list[Path]]): Additional patches to apply.
            jobs (Optional[int]): Amount of jobs to use for the building.
            configure_flags (str | None): additional flags to pass to the configure script or cmake.
            force (bool): Build the specified compiler even if it has been cached before.

        Returns:
            Path: `cache_prefix`/projectname-commit
        """
        repo = select_repo(project, self.llvm_repo, self.gcc_repo)
        commit = repo.rev_to_commit(rev.strip())
        if built_compiler_info := self.cstore.get_built_compiler(project, commit):
            return built_compiler_info
        if self.cstore.has_previously_failed_to_build(project, commit) and not force:
            raise BuildException(
                f"Compiler {project} {commit} has previously failed to build.\n"
                "Run with force==True (--force) to try again."
            )

        if not jobs and not self.jobs:
            jobs = multiprocessing.cpu_count()
        elif not jobs:
            jobs = self.jobs

        try:
            install_prefix = build_and_install_compiler(
                project=project,
                rev=rev,
                cache_prefix=self.cache_prefix,
                llvm_repo=self.llvm_repo,
                gcc_repo=self.gcc_repo,
                patchdb=self.patchdb,
                jobs=jobs,
                additional_patches=additional_patches,
                configure_flags=configure_flags,
                logdir=self.logdir,
            )
        except BuildException as e:
            self.cstore.add_failed_to_build_compiler(project, commit)
            raise e

        compiler_info = BuiltCompilerInfo(project, install_prefix, commit)
        self.cstore.add_compiler(compiler_info)
        return compiler_info

import logging
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Optional, TextIO

from ccbuilder.patcher.patchdatabase import PatchDB
from ccbuilder.utils.repository import Repo, Revision, Commit
from ccbuilder.utils.utils import (
    CompilerProject,
    run_cmd_to_logfile,
    select_repo,
    get_compiler_project,
)


class BuildException(Exception):
    pass


@dataclass
class BuildContext:
    install_prefix: Path
    success_indicator: Path
    project: CompilerProject
    commit_to_build: Commit
    logdir: Path

    def __enter__(self) -> tuple[Path, TextIO]:
        self.build_dir = tempfile.mkdtemp()
        os.makedirs(self.install_prefix, exist_ok=True)

        # Write worker PID
        with open(self.install_prefix / "WORKER_PID", "w") as f:
            f.write(str(os.getpid()))

        self.starting_cwd = os.getcwd()
        os.chdir(self.build_dir)

        # Build log file
        current_time = time.strftime("%Y%m%d-%H%M%S")
        name = self.project.to_string()
        build_log_path = (
            self.logdir / f"{current_time}-{name}-{self.commit_to_build}.log"
        )
        self.build_log = open(build_log_path, "a")
        logging.info(f"Build log at {build_log_path}")

        return (Path(self.build_dir), self.build_log)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        exc_traceback: Optional[TracebackType],
    ) -> None:
        self.build_log.close()
        shutil.rmtree(self.build_dir)
        os.chdir(self.starting_cwd)
        os.remove(self.install_prefix / "WORKER_PID")

        # Build was not successful
        if not self.success_indicator.exists():
            # remove cache entry
            shutil.rmtree(self.install_prefix)


def apply_patches(git_dir: Path, patches: list[Path]) -> bool:
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
    patches = patchdb.required_patches(commit_to_patch) + additional_patches
    if patches:
        if not apply_patches(working_dir, patches):
            raise BuildException(f"Could not apply patches: {patches}")


def llvm_build_and_install(install_prefix: Path, cores: int, log_file: TextIO) -> None:
    os.chdir("build")
    logging.debug("LLVM: Starting cmake")
    cmake_cmd = f"cmake ../llvm -G Ninja -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_PROJECTS=clang -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_TESTS=OFF -DLLVM_USE_NEWPM=ON -DLLVM_TARGETS_TO_BUILD=X86 -DCMAKE_INSTALL_PREFIX={install_prefix} -DLLVM_LINK_LLVM_DYLIB=ON -DLLVM_BUILD_LLVM_DYLIB=ON"

    run_cmd_to_logfile(
        cmake_cmd, additional_env={"CC": "clang", "CXX": "clang++"}, log_file=log_file
    )

    logging.debug("LLVM: Starting to build...")
    run_cmd_to_logfile(f"ninja -j {cores} install", log_file=log_file)


def gcc_build_and_install(install_prefix: Path, cores: int, log_file: TextIO) -> None:
    pre_cmd = "./contrib/download_prerequisites"
    logging.debug("GCC: Starting download_prerequisites")
    run_cmd_to_logfile(pre_cmd, log_file=log_file)

    os.chdir("build")
    logging.debug("GCC: Starting configure")
    configure_cmd = (
        "../configure --disable-multilib --disable-bootstrap"
        f" --enable-languages=c,c++ --prefix={install_prefix}"
    )
    run_cmd_to_logfile(configure_cmd, log_file=log_file)

    logging.debug("GCC: Starting to build...")
    run_cmd_to_logfile(f"make -j {cores}", log_file=log_file)
    run_cmd_to_logfile("make install-strip", log_file=log_file)


def _run_build_and_install(
    project: CompilerProject, install_path: Path, cores: int, log_file: TextIO
) -> Path:
    os.makedirs("build")
    try:
        match project:
            case CompilerProject.GCC:
                gcc_build_and_install(install_path, cores, log_file)
            case CompilerProject.LLVM:
                llvm_build_and_install(install_path, cores, log_file)
    except subprocess.CalledProcessError as e:
        raise BuildException(f"Build failed: {e}")
    return install_path


def get_install_path(
    cache_prefix: Path, project: CompilerProject, commit: Commit
) -> Path:
    match project:
        case CompilerProject.LLVM:
            install_name_prefix = "clang"
        case CompilerProject.GCC:
            install_name_prefix = "gcc"
    return cache_prefix / f"{install_name_prefix}-{commit}"


def get_executable_postfix(project: CompilerProject) -> Path:
    match project:
        case CompilerProject.LLVM:
            executable_name = "clang"
        case CompilerProject.GCC:
            executable_name = "gcc"
    return Path("bin") / executable_name


def build_and_install_compiler(
    project: CompilerProject,
    rev: Revision,
    cache_prefix: Path,
    llvm_repo: Repo,
    gcc_repo: Repo,
    patchdb: PatchDB,
    get_executable: bool = False,
    jobs: Optional[int] = None,
    additional_patches: Optional[list[Path]] = None,
    logdir: Optional[Path] = None,
) -> Path:

    repo = select_repo(project, llvm_repo, gcc_repo)
    commit = repo.rev_to_commit(rev)
    install_prefix = get_install_path(cache_prefix, project, commit)
    success_indicator = install_prefix / "DONE"

    if not logdir:
        logdir = cache_prefix / "logs"
    logdir.mkdir(exist_ok=True)
    with BuildContext(install_prefix, success_indicator, project, commit, logdir) as (
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
        )
        success_indicator.touch()
        if get_executable:
            return res / get_executable_postfix(project)
        return res


class BuilderWithoutCache:
    def __init__(
        self,
        cache_prefix: Path,
        gcc_repo: Repo,
        llvm_repo: Repo,
        patchdb: Optional[PatchDB] = None,
        jobs: Optional[int] = None,
        logdir: Optional[Path] = None,
    ):
        self.cache_prefix = cache_prefix
        self.gcc_repo = gcc_repo
        self.llvm_repo = llvm_repo

        pdb = patchdb if patchdb else PatchDB()
        self.patchdb = pdb
        self.jobs = jobs if jobs else multiprocessing.cpu_count()
        self.logdir = logdir

    def build(
        self,
        project: CompilerProject,
        rev: Revision,
        get_executable: bool = False,
        additional_patches: Optional[list[Path]] = None,
        jobs: Optional[int] = None,
    ) -> Path:
        if not jobs and not self.jobs:
            jobs = multiprocessing.cpu_count()
        elif not jobs:
            jobs = self.jobs

        return build_and_install_compiler(
            project=project,
            rev=rev,
            cache_prefix=self.cache_prefix,
            llvm_repo=self.llvm_repo,
            gcc_repo=self.gcc_repo,
            patchdb=self.patchdb,
            get_executable=get_executable,
            jobs=jobs,
            additional_patches=additional_patches,
            logdir=self.logdir,
        )


def worker_alive(worker_indicator: Path) -> bool:
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


class Builder(BuilderWithoutCache):
    def build(
        self,
        project: CompilerProject,
        rev: str,
        get_executable: bool = False,
        additional_patches: Optional[list[Path]] = None,
        jobs: Optional[int] = None,
        force: bool = False,
    ) -> Path:

        repo = select_repo(project, self.llvm_repo, self.gcc_repo)
        commit = repo.rev_to_commit(rev)
        install_path = get_install_path(self.cache_prefix, project, commit)
        success_indicator = install_path / "DONE"
        worker_indicator = install_path / "WORKER_PID"

        postfix: Path = get_executable_postfix(project) if get_executable else Path()

        if not force:
            if success_indicator.exists():
                return install_path / postfix
            elif install_path.exists() and worker_alive(worker_indicator):

                logging.info(
                    f"This compiler seems to be built by some other worker. Need to wait. If there is no other worker, abort this command and run ccbuilder cache clean."
                )
                start_time = time.time()
                counter = 0
                while (
                    worker_alive(worker_indicator)
                    and not success_indicator.exists()
                    and install_path.exists()
                ):
                    time.sleep(1)
                    if time.time() - start_time > 15 * 60:
                        counter += 1
                        logging.info(
                            f"{counter*15} minutes have passed waiting. Maybe the cache is in an inconsistent state."
                        )
                        start_time = time.time()

                if success_indicator.exists():
                    return install_path / postfix

                # Someone was building but did not leave the success_indicator
                # so something went wrong with the build.
                raise BuildException(f"Other build attempt failed for {install_path}")

        return super().build(
            project,
            rev,
            get_executable=get_executable,
            additional_patches=additional_patches,
            jobs=jobs,
        )

    def build_name(
        self,
        name: str,
        rev: str,
        get_executable: bool = False,
        additional_patches: Optional[list[Path]] = None,
        jobs: Optional[int] = None,
        force: bool = False,
    ) -> Path:
        return self.build(
            get_compiler_project(name),
            rev=rev,
            get_executable=get_executable,
            additional_patches=additional_patches,
            jobs=jobs,
            force=force,
        )

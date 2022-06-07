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
from ccbuilder.utils.utils import (
    Compiler,
    CompilerConfig,
    get_compiler_config,
    run_cmd_to_logfile,
)


class BuildException(Exception):
    pass


@dataclass
class CompilerBuildJob:
    compiler_config: CompilerConfig
    commit_to_build: str
    patchdb: PatchDB


@dataclass
class BuildContext:
    cache_prefix: Path
    success_indicator: Path
    job: CompilerBuildJob
    logdir: Path

    def __enter__(self) -> tuple[Path, TextIO]:
        self.build_dir = tempfile.mkdtemp()
        os.makedirs(self.cache_prefix, exist_ok=True)

        # Write worker PID
        with open(self.cache_prefix / "WORKER_PID", "w") as f:
            f.write(str(os.getpid()))

        self.starting_cwd = os.getcwd()
        os.chdir(self.build_dir)

        # Build log file
        current_time = time.strftime("%Y%m%d-%H%M%S")
        name = self.job.compiler_config.compiler.to_string()
        build_log_path = (
            self.logdir / f"{current_time}-{name}-{self.job.commit_to_build}.log"
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
        os.remove(self.cache_prefix / "WORKER_PID")

        # Build was not successful
        if not self.success_indicator.exists():
            # remove cache entry
            shutil.rmtree(self.cache_prefix)


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
    job: CompilerBuildJob, working_dir: Path, additional_patches: list[Path] = []
) -> None:
    patches = job.patchdb.required_patches(job.commit_to_build) + additional_patches
    if patches:
        if not apply_patches(working_dir, patches):
            raise BuildException(f"Could not apply patches: {patches}")


def llvm_build_and_install(prefix: Path, cores: int, log_file: TextIO) -> None:
    os.chdir("build")
    logging.debug("LLVM: Starting cmake")
    cmake_cmd = f"cmake ../llvm -G Ninja -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_PROJECTS=clang -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_TESTS=OFF -DLLVM_USE_NEWPM=ON -DLLVM_TARGETS_TO_BUILD=X86 -DCMAKE_INSTALL_PREFIX={prefix} -DLLVM_LINK_LLVM_DYLIB=ON -DLLVM_BUILD_LLVM_DYLIB=ON"

    run_cmd_to_logfile(
        cmake_cmd, additional_env={"CC": "clang", "CXX": "clang++"}, log_file=log_file
    )

    logging.debug("LLVM: Starting to build...")
    run_cmd_to_logfile(f"ninja -j {cores} install", log_file=log_file)


def gcc_build_and_install(prefix: Path, cores: int, log_file: TextIO) -> None:
    pre_cmd = "./contrib/download_prerequisites"
    logging.debug("GCC: Starting download_prerequisites")
    run_cmd_to_logfile(pre_cmd, log_file=log_file)

    os.chdir("build")
    logging.debug("GCC: Starting configure")
    configure_cmd = (
        "../configure --disable-multilib --disable-bootstrap"
        f" --enable-languages=c,c++ --prefix={prefix}"
    )
    run_cmd_to_logfile(configure_cmd, log_file=log_file)

    logging.debug("GCC: Starting to build...")
    run_cmd_to_logfile(f"make -j {cores}", log_file=log_file)
    run_cmd_to_logfile("make install-strip", log_file=log_file)


def get_install_path_from_job(job: CompilerBuildJob, prefix: Path) -> Path:
    if job.compiler_config.compiler == Compiler.GCC:
        install_path = prefix / f"gcc-{job.commit_to_build}"
    elif job.compiler_config.compiler == Compiler.LLVM:
        install_path = prefix / f"clang-{job.commit_to_build}"
    else:
        raise Exception("Unknown compiler type!")
    return install_path


def _run_build_and_install(
    job: CompilerBuildJob, prefix: Path, cores: int, log_file: TextIO
) -> Path:
    os.makedirs("build")
    install_path = get_install_path_from_job(job, prefix)
    try:
        if job.compiler_config.compiler == Compiler.GCC:
            gcc_build_and_install(install_path, cores, log_file)
        else:
            assert job.compiler_config.compiler == Compiler.LLVM
            llvm_build_and_install(install_path, cores, log_file)
    except subprocess.CalledProcessError as e:
        raise BuildException(f"Build failed: {e}")
    return install_path


def build_and_install_compiler(
    job: CompilerBuildJob,
    prefix: Path,
    cores: int,
    additional_patches: list[Path] = [],
    logdir: Optional[Path] = None,
) -> Path:
    install_path = get_install_path_from_job(job, prefix)
    success_indicator = install_path / "DONE"

    if not logdir:
        logdir = prefix / "logs"
    logdir.mkdir(exist_ok=True)
    with BuildContext(install_path, success_indicator, job, logdir) as (
        tmpdir,
        build_log,
    ):
        run_cmd_to_logfile(
            f"git -C {str(job.compiler_config.repo.path)} worktree"
            f" add {tmpdir} {job.commit_to_build} -f",
            log_file=build_log,
        )
        patch_if_necessary(job, Path(tmpdir), additional_patches)
        res = _run_build_and_install(job, prefix, cores, build_log)
        success_indicator.touch()
        return res


def get_compiler_build_job(
    compiler_config: CompilerConfig, revision: str, patchdb: PatchDB
) -> CompilerBuildJob:
    return CompilerBuildJob(
        compiler_config, compiler_config.repo.rev_to_commit(revision), patchdb
    )


class Builder:
    def __init__(
        self,
        cache_prefix: Path,
        patchdb: Optional[PatchDB] = None,
        cores: Optional[int] = None,
        logdir: Optional[Path] = None,
    ):
        self.cache_prefix = cache_prefix
        pdb = patchdb if patchdb else PatchDB()
        self.patchdb = pdb
        self.cores = cores if cores else multiprocessing.cpu_count()
        self.logdir = logdir

    def build_job(
        self, job: CompilerBuildJob, additional_patches: list[Path] = []
    ) -> Path:
        return build_and_install_compiler(
            job,
            self.cache_prefix,
            self.cores,
            additional_patches=additional_patches,
            logdir=self.logdir,
        )

    def build_rev_with_config(
        self,
        compiler_config: CompilerConfig,
        revision: str,
        additional_patches: list[Path] = [],
    ) -> Path:
        job = get_compiler_build_job(
            compiler_config, revision=revision, patchdb=self.patchdb
        )
        return self.build_job(job, additional_patches=additional_patches)

    def build_rev_with_name(
        self, compiler_name: str, revision: str, additional_patches: list[Path] = []
    ) -> Path:
        compiler_config = get_compiler_config(compiler_name, self.cache_prefix)
        return self.build_rev_with_config(
            compiler_config, revision=revision, additional_patches=additional_patches
        )


def get_compiler_executable_from_job(job: CompilerBuildJob, bldr: Builder) -> Path:
    base = bldr.build_job(job)
    if job.compiler_config.compiler == Compiler.GCC:
        return base / "bin" / "gcc"
    else:
        return base / "bin" / "clang"


def get_compiler_executable_from_revision_with_config(
    compiler_config: CompilerConfig, revision: str, bldr: Builder
) -> Path:
    job = get_compiler_build_job(
        compiler_config, revision=revision, patchdb=bldr.patchdb
    )
    return get_compiler_executable_from_job(job, bldr)


def get_compiler_executable_from_revision_with_name(
    compiler_name: str, revision: str, bldr: Builder
) -> Path:
    compiler_config = get_compiler_config(compiler_name, bldr.cache_prefix)
    return get_compiler_executable_from_revision_with_config(
        compiler_config, revision, bldr
    )

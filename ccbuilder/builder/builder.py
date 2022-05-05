import time
import os
import logging
import subprocess
import multiprocessing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, TextIO

from ccbuilder.utils.utils import (
    pushd,
    run_cmd,
    run_cmd_to_logfile,
    CompilerConfig,
    Compiler,
    get_compiler_config,
)
from ccbuilder.patcher.patchdatabase import PatchDB


class BuildException(Exception):
    pass


@dataclass
class CompilerBuildJob:
    compiler_config: CompilerConfig
    commit_to_build: str
    patchdb: PatchDB


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
            raise BuildException("Could not apply patches: {patches}")


def llvm_build_and_install(prefix: Path, cores: int, log_file: TextIO) -> None:
    os.chdir("build")
    logging.debug("LLVM: Starting cmake")
    cmake_cmd = (
        "cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_PROJECTS=clang"
        " -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_TESTS=OFF -DLLVM_USE_NEWPM=ON"
        f" -DLLVM_TARGETS_TO_BUILD=X86 -DCMAKE_INSTALL_PREFIX={prefix} ../llvm"
    )
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
    run_cmd_to_logfile("make install", log_file=log_file)


def get_install_path_from_job(job: CompilerBuildJob, prefix: Path) -> Path:
    if job.compiler_config.compiler == Compiler.GCC:
        install_path = prefix / f"gcc-{job.commit_to_build}"
    elif job.compiler_config.compiler == Compiler.LLVM:
        install_path = prefix / f"llvm-{job.commit_to_build}"
    else:
        raise Exception("Unknown compiler type!")
    return install_path


def _run_build_and_install(
    job: CompilerBuildJob, prefix: Path, cores: int, log_file: TextIO
) -> Path:
    os.makedirs("build")
    install_path = get_install_path_from_job(job, prefix)
    if job.compiler_config.compiler == Compiler.GCC:
        gcc_build_and_install(install_path, cores, log_file)
    else:
        assert job.compiler_config.compiler == Compiler.LLVM
        llvm_build_and_install(install_path, cores, log_file)
    return install_path


def build_and_install_compiler(
    job: CompilerBuildJob, prefix: Path, cores: int, additional_patches: list[Path] = []
) -> Path:

    current_time = time.strftime("%Y%m%d-%H%M%S")
    build_log_path = prefix / "logs"
    build_log_path.mkdir(exist_ok=True)
    build_log_path = (
        build_log_path
        / f"{current_time}-{job.compiler_config.name}-{job.commit_to_build}.log"
    )

    build_log = open(build_log_path, "a")
    logging.info(f"Build log at {build_log_path}")
    with TemporaryDirectory() as tmpdir:
        run_cmd_to_logfile(
            f"git -C {str(job.compiler_config.repo.path)} worktree"
            f" add {tmpdir} {job.commit_to_build} -f",
            log_file=build_log,
        )
        with pushd(tmpdir):
            patch_if_necessary(job, Path(tmpdir), additional_patches)
            return _run_build_and_install(job, prefix, cores, build_log)


def get_compiler_build_job(
    compiler_config: CompilerConfig, revision: str, patchdb: PatchDB
) -> CompilerBuildJob:
    return CompilerBuildJob(
        compiler_config, compiler_config.repo.rev_to_commit(revision), patchdb
    )


class Builder:
    def __init__(self, prefix: Path, patchdb: PatchDB, cores: Optional[int] = None):
        self.prefix = prefix
        self.patchdb = patchdb
        if cores is None:
            self.cores = cores if cores else multiprocessing.cpu_count()

    def build_job(
        self, job: CompilerBuildJob, additional_patches: list[Path] = []
    ) -> Path:
        return build_and_install_compiler(
            job, self.prefix, self.cores, additional_patches=additional_patches
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
        compiler_config = get_compiler_config(compiler_name, self.prefix)
        return self.build_rev_with_config(
            compiler_config, revision=revision, additional_patches=additional_patches
        )

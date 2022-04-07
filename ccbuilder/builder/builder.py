import os
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from ccbuilder.utils.utils import pushd, run_cmd, CompilerConfig, Compiler
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


def llvm_build_and_install(prefix: Path, cores: int) -> None:
    os.chdir("build")
    logging.debug("LLVM: Starting cmake")
    cmake_cmd = (
        "cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DLLVM_ENABLE_PROJECTS=clang"
        " -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_TESTS=OFF -DLLVM_USE_NEWPM=ON"
        f" -DLLVM_TARGETS_TO_BUILD=X86 -DCMAKE_INSTALL_PREFIX={prefix} ../llvm"
    )
    run_cmd(
        cmake_cmd,
        additional_env={"CC": "clang", "CXX": "clang++"},
    )

    logging.debug("LLVM: Starting to build...")
    run_cmd(
        f"ninja -j {cores} install",
    )


def gcc_build_and_install(prefix: Path, cores: int) -> None:
    pre_cmd = "./contrib/download_prerequisites"
    logging.debug("GCC: Starting download_prerequisites")
    run_cmd(pre_cmd)

    os.chdir("build")
    logging.debug("GCC: Starting configure")
    configure_cmd = (
        "../configure --disable-multilib --disable-bootstrap"
        f" --enable-languages=c,c++ --prefix={prefix}"
    )
    run_cmd(configure_cmd)

    logging.debug("GCC: Starting to build...")
    run_cmd(f"make -j {cores}")
    run_cmd("make install")


def _run_build_and_install(job: CompilerBuildJob, prefix: Path, cores: int) -> None:
    os.makedirs("build")
    if job.compiler_config.compiler == Compiler.GCC:
        gcc_build_and_install(prefix, cores)
    else:
        assert job.compiler_config.compiler == Compiler.LLVM
        llvm_build_and_install(prefix, cores)


def build_and_install_compiler(
    job: CompilerBuildJob, prefix: Path, cores: int, additional_patches: list[Path] = []
) -> None:
    with TemporaryDirectory() as tmpdir:
        run_cmd(
            f"git -C {str(job.compiler_config.repo.path)} worktree"
            f" add {tmpdir} {job.commit_to_build} -f"
        )
        with pushd(tmpdir):
            patch_if_necessary(job, Path(tmpdir), additional_patches)
            _run_build_and_install(job, prefix, cores)


def get_compiler_build_job(
    compiler_config: CompilerConfig, revision: str, patchdb: PatchDB
) -> CompilerBuildJob:
    return CompilerBuildJob(
        compiler_config, compiler_config.repo.rev_to_commit(revision), patchdb
    )

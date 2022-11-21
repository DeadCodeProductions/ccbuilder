from __future__ import annotations

import os
import sys
import shlex
import subprocess
from shutil import copy
from enum import Enum
from pathlib import Path
from typing import TextIO, Literal, Union, Optional
from dataclasses import dataclass

import ccbuilder.utils.repository as repository


@dataclass(kw_only=True, frozen=True)
class ProcessResult:
    return_value: int


def run_cmd(
    cmd: str,
    capture_output: bool = True,
    additional_env: dict[str, str] = {},
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    ignore_output: bool = False,
) -> str:
    env = os.environ.copy()
    env.update(additional_env)

    _stdout: TextIO | int = stdout
    _stderr: TextIO | int = stderr

    # capture_output is more powerful than ignore_output
    # due to the implementation of subprocess.run
    if ignore_output:
        _stdout = subprocess.DEVNULL
        _stderr = subprocess.DEVNULL

    res = subprocess.run(
        shlex.split(cmd),
        capture_output=capture_output,
        stdout=stdout,
        stderr=stderr,
        check=True,
        env=env,
    )
    if capture_output:
        return res.stdout.decode("utf-8").strip()
    return ""


def run_cmd_to_logfile(
    cmd: str, log_file: TextIO, additional_env: dict[str, str] = {}
) -> None:
    env = os.environ.copy()
    env.update(additional_env)
    subprocess.run(
        shlex.split(cmd),
        check=True,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        capture_output=False,
    )


class CompilerProject(Enum):
    GCC = 0
    LLVM = 1

    def to_string(self) -> str:
        return "gcc" if self == CompilerProject.GCC else "clang"


def select_repo(
    project: CompilerProject, llvm_repo: repository.Repo, gcc_repo: repository.Repo
) -> repository.Repo:
    match project:
        case CompilerProject.LLVM:
            repo = llvm_repo
        case CompilerProject.GCC:
            repo = gcc_repo
    return repo


def get_compiler_project(project_name: str) -> CompilerProject:
    """Get the `CompilerProject` from the project name.

    Args:
        project_name (str):

    Returns:
        CompilerProject: Project corresponding to `project_name`.
    """
    match project_name:
        case "gcc":
            return CompilerProject.GCC
        case "llvm" | "clang":
            return CompilerProject.LLVM
        case _:
            raise Exception(f"Unknown compiler project {project_name}!")


def get_compiler_info(
    project_name: Union[Literal["llvm"], Literal["gcc"], Literal["clang"]],
    repo_dir_prefix: Path,
) -> tuple[CompilerProject, repository.Repo]:
    match project_name:
        case "gcc":
            repo = repository.Repo(repo_dir_prefix / "gcc", "master")
            return CompilerProject.GCC, repo
        case "llvm" | "clang":
            repo = repository.Repo(repo_dir_prefix / "llvm-project", "main")
            return CompilerProject.LLVM, repo
        case _:
            raise Exception(f"Unknown compiler project {project_name}!")


def find_cached_revisions(
    project: CompilerProject, cache_prefix: Path
) -> list[repository.Commit]:
    """Get all commits of `project` that have been built and cached in `cache_prefix`.

    Args:
        project (CompilerProject): Project to get commits for.
        cache_prefix (Path): Path to cache.

    Returns:
        list[repository.Commit]:
    """
    match project:
        case CompilerProject.GCC:
            compiler_name = "gcc"
        case CompilerProject.LLVM:
            compiler_name = "clang"

    compilers: list[str] = []

    for entry in Path(cache_prefix).iterdir():
        if entry.is_symlink() or not entry.stem.startswith(compiler_name):
            continue
        if not (entry / "bin" / compiler_name).exists():
            continue
        rev = str(entry).split("-")[-1]
        compilers.append(rev)
    return compilers


CompilerReleases = {
    CompilerProject.GCC: [
        "releases/gcc-12.1.0",
        "releases/gcc-11.3.0",
        "releases/gcc-11.2.0",
        "releases/gcc-11.1.0",
        "releases/gcc-10.4.0",
        "releases/gcc-10.3.0",
        "releases/gcc-10.2.0",
        "releases/gcc-10.1.0",
        "releases/gcc-9.5.0",
        "releases/gcc-9.4.0",
        "releases/gcc-9.3.0",
        "releases/gcc-9.2.0",
        "releases/gcc-9.1.0",
        "releases/gcc-8.5.0",
        "releases/gcc-8.4.0",
        "releases/gcc-8.3.0",
        "releases/gcc-8.2.0",
        "releases/gcc-8.1.0",
        "releases/gcc-7.5.0",
        "releases/gcc-7.4.0",
        "releases/gcc-7.3.0",
        "releases/gcc-7.2.0",
    ],
    CompilerProject.LLVM: [
        "llvmorg-14.0.6",
        "llvmorg-14.0.5",
        "llvmorg-14.0.4",
        "llvmorg-14.0.3",
        "llvmorg-14.0.2",
        "llvmorg-14.0.1",
        "llvmorg-14.0.0",
        "llvmorg-13.0.1",
        "llvmorg-13.0.0",
        "llvmorg-12.0.1",
        "llvmorg-12.0.0",
        "llvmorg-11.1.0",
        "llvmorg-11.0.1",
        "llvmorg-11.0.0",
        "llvmorg-10.0.1",
        "llvmorg-10.0.0",
        "llvmorg-9.0.1",
        "llvmorg-9.0.0",
        "llvmorg-8.0.1",
        "llvmorg-8.0.0",
        "llvmorg-7.1.0",
        "llvmorg-7.0.1",
        "llvmorg-7.0.0",
        "llvmorg-6.0.1",
        "llvmorg-6.0.0",
        "llvmorg-5.0.2",
        "llvmorg-5.0.1",
        "llvmorg-5.0.0",
        "llvmorg-4.0.1",
        "llvmorg-4.0.0",
    ],
}

MajorCompilerReleases = {
    CompilerProject.GCC: [
        "releases/gcc-12.1.0",
        "releases/gcc-11.3.0",
        "releases/gcc-10.4.0",
        "releases/gcc-9.5.0",
        "releases/gcc-8.5.0",
        "releases/gcc-7.5.0",
    ],
    CompilerProject.LLVM: [
        "llvmorg-14.0.6",
        "llvmorg-13.0.1",
        "llvmorg-12.0.1",
        "llvmorg-11.1.0",
        "llvmorg-10.0.1",
        "llvmorg-9.0.1",
        "llvmorg-8.0.1",
        "llvmorg-7.1.0",
        "llvmorg-6.0.1",
        "llvmorg-5.0.2",
        "llvmorg-4.0.1",
    ],
}


def initialize_repos(repos_dir: str) -> None:
    repos_path = Path(repos_dir)
    repos_path.mkdir(parents=True, exist_ok=True)
    llvm = repos_path / "llvm-project"
    if not llvm.exists():
        print("Cloning LLVM...")
        run_cmd(f"git clone https://github.com/llvm/llvm-project.git {llvm}")
    gcc = repos_path / "gcc"
    if not gcc.exists():
        print("Cloning GCC...")
        run_cmd(f"git clone git://gcc.gnu.org/git/gcc.git {gcc}")


def initialize_patches_dir(patches_dir: str) -> None:
    patches_path = Path(patches_dir)
    if not patches_path.exists():
        _ROOT = Path(__file__).parent.parent.absolute()
        patches_path.mkdir(parents=True, exist_ok=True)
        patches_source_dir = _ROOT / "data" / "patches"
        for entry in patches_source_dir.iterdir():
            copy(entry, patches_path / entry.name)

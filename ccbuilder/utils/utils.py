from __future__ import annotations

import os
import shlex
import subprocess
from enum import Enum
from pathlib import Path
from typing import TextIO, Literal, Union

import ccbuilder.utils.repository as repository


def run_cmd(
    cmd: str, capture_output: bool = False, additional_env: dict[str, str] = {}
) -> str:
    env = os.environ.copy()
    env.update(additional_env)
    res = subprocess.run(
        shlex.split(cmd), capture_output=capture_output, check=True, env=env
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


def get_repo(project: CompilerProject, path_to_repo: Path) -> repository.Repo:
    match project:
        case CompilerProject.LLVM:
            return repository.Repo.llvm_repo(path_to_repo)
        case CompilerProject.GCC:
            return repository.Repo.gcc_repo(path_to_repo)
    raise Exception("Unreachable")


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
        "releases/gcc-10.3.0",
        "releases/gcc-9.5.0",
        "releases/gcc-8.5.0",
        "releases/gcc-7.5.0",
    ],
    CompilerProject.LLVM: [
        "llvmorg-14.0.5",
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

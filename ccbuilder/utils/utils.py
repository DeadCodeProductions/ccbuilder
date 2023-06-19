from __future__ import annotations

import os
import shlex
import subprocess
from shutil import copytree
from enum import Enum
from pathlib import Path
from typing import TextIO, Literal, Union

import diopter.repository as repository
from diopter.compiler import CompilerProject


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
            repo = repository.Repo(
                repo_dir_prefix / "gcc", repository.Revision("master")
            )
            return CompilerProject.GCC, repo
        case "llvm" | "clang":
            repo = repository.Repo(
                repo_dir_prefix / "llvm-project", repository.Revision("main")
            )
            return CompilerProject.LLVM, repo
        case _:
            raise Exception(f"Unknown compiler project {project_name}!")


def initialize_repos(repos_path: Path) -> None:
    repos_path.mkdir(parents=True, exist_ok=True)
    llvm = repos_path / "llvm-project"
    if not llvm.exists():
        print("Cloning LLVM...")
        run_cmd(f"git clone https://github.com/llvm/llvm-project.git {llvm}")
    gcc = repos_path / "gcc"
    if not gcc.exists():
        print("Cloning GCC...")
        run_cmd(f"git clone git://gcc.gnu.org/git/gcc.git {gcc}")


def initialize_patches_dir(patches_path: Path) -> None:
    if not patches_path.exists():
        _ROOT = Path(__file__).parent.parent.absolute()
        patches_path.mkdir(parents=True, exist_ok=True)
        patches_source_dir = _ROOT / "data" / "patches"
        if not (patches_path / "llvm").exists():
            copytree(
                patches_source_dir / "llvm", patches_path / "llvm", dirs_exist_ok=True
            )
        if not (patches_path / "gcc").exists():
            copytree(
                patches_source_dir / "gcc", patches_path / "gcc", dirs_exist_ok=True
            )

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TextIO

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


class Compiler(Enum):
    GCC = 0
    LLVM = 1


@dataclass
class CompilerConfig:
    compiler: Compiler
    name: str
    repo: repository.Repo
    releases: list[str]


releases = {
    "gcc": [
        "releases/gcc-11.2.0",
        "releases/gcc-11.1.0",
        "releases/gcc-10.3.0",
        "releases/gcc-10.2.0",
        "releases/gcc-10.1.0",
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
    "llvm": [
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


def get_compiler_config(compiler_name: str, repo_prefix_path: Path) -> CompilerConfig:
    assert compiler_name in ["llvm", "gcc"]
    repo_path = repo_prefix_path / ("gcc" if compiler_name == "gcc" else "llvm-project")
    main_branch = "master" if compiler_name == "gcc" else "main"
    return CompilerConfig(
        Compiler.GCC if compiler_name == "gcc" else Compiler.LLVM,
        compiler_name,
        repository.Repo(repo_path, main_branch),
        releases[compiler_name],
    )

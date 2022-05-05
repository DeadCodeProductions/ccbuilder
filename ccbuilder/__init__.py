import logging
import shlex
from argparse import ArgumentParser, Namespace
from multiprocessing import cpu_count
from subprocess import run
from pathlib import Path

from ccbuilder.builder.builder import (
    get_compiler_build_job,
    build_and_install_compiler,
    CompilerBuildJob,
    Builder,
    get_install_path_from_job,
    BuildException,
)

from ccbuilder.utils.utils import get_compiler_config, CompilerConfig, Compiler
from ccbuilder.utils.repository import Repo
from ccbuilder.patcher.patchdatabase import PatchDB
from ccbuilder.patcher.patcher import Patcher

__all__ = [
    "get_compiler_config",
    "CompilerConfig",
    "Compiler",
    "Repo",
    "PatchDB",
    "Patcher",
    "get_compiler_build_job",
    "build_and_install_compiler",
    "CompilerBuildJob",
    "Builder",
    "get_install_path_from_job",
    "BuildException",
]

_ROOT = Path(__file__).parent.absolute()


def _initialize(args: Namespace) -> None:
    from shutil import copy

    repos_path = Path(args.repos_dir)
    repos_path.mkdir(parents=True, exist_ok=True)
    llvm = repos_path / "llvm-project"
    if not llvm.exists():
        print("Cloning LLVM...")
        run(
            shlex.split(f"git clone https://github.com/llvm/llvm-project.git {llvm}"),
            check=True,
        )
    gcc = repos_path / "gcc"
    if not gcc.exists():
        print("Cloning GCC...")
        run(
            shlex.split(f"git clone git://gcc.gnu.org/git/gcc.git {gcc}"),
            check=True,
        )
    patches_path = Path(args.patches_dir)
    if not patches_path.exists():
        patches_path.mkdir(parents=True, exist_ok=True)
        patches_source_dir = _ROOT / "data" / "patches"
        for entry in patches_source_dir.iterdir():
            copy(entry, patches_path / entry.name)


def _parse_args() -> Namespace:
    parser = ArgumentParser("ccbuilder")
    subparsers = parser.add_subparsers(dest="command")

    parser.add_argument(
        "-ll",
        "--log-level",
        type=str,
        choices=("debug", "info", "warning", "error", "critical"),
        help="Log level",
    )

    parser.add_argument(
        "--pull",
        action="store_true",
        default=False,
        help="Update the GCC and LLVM repositories",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=cpu_count(),
        help="Number of build jobs (default:use all cores)",
    )
    default_prefix = str(Path.home() / ".cache" / "ccbuilder-compilers")
    parser.add_argument(
        "--prefix",
        type=str,
        default=default_prefix,
        help=f"Installation prefix (default: {default_prefix})",
    )
    default_patch_dir = str(Path.home() / ".cache" / "ccbuilder-patches")
    parser.add_argument(
        "--patches-dir",
        type=str,
        default=default_patch_dir,
        help=f"Path to the patches directory (default: {default_patch_dir})",
    )
    default_repos_dir = str(Path.home() / ".cache" / "ccbuilder-repos")
    parser.add_argument(
        "--repos-dir",
        type=str,
        default=default_repos_dir,
        help=f"Path to the directory with the compiler repositories (default: {default_repos_dir})",
    )
    parser.add_argument(
        "compiler", choices=["llvm", "gcc"], help="Which compiler to build and install"
    )
    parser.add_argument("revision", type=str, help="Target revision")

    parser.add_argument(
        "--patches",
        nargs="+",
        help="For the 'patcher', this defines which patch(es) to apply. For the 'builder', \
            these are the additional patches to apply to the already existing patches from the PatchDB.",
        type=str,
    )

    build = subparsers.add_parser("build")
    # add "build-releases" option here

    patch = subparsers.add_parser("patch")
    patch.add_argument(
        "revision", type=str, help="Which revision to patch/Broken revision"
    )

    patch_command = patch.add_mutually_exclusive_group(required=True)
    patch_command.add_argument(
        "--find-range",
        help="Try to find the range where a patch is required",
        action="store_true",
    )
    patch_command.add_argument(
        "--find-introducer",
        help="Try to find the introducer commit of a build failure.",
        action="store_true",
    )

    args = parser.parse_args()

    if args.log_level is not None:
        try:
            num_lvl = getattr(logging, args.log_level.upper())
            logging.basicConfig(level=num_lvl)
        except AttributeError:
            print(f"No such log level {args.log_level.upper()}")
            exit(1)
    return args


def run_as_module() -> None:
    args = _parse_args()
    _initialize(args)
    cconfig = get_compiler_config(args.compiler, args.repos_dir)

    patchdb = PatchDB(Path(args.patches_dir) / "patchdb.json")
    bldr = Builder(Path(args.prefix.strip()), patchdb)

    patches = [Path(p.strip()).absolute() for p in args.patches] if args.patches else []
    if args.pull:
        cconfig.repo.pull()

    if args.command == "build":
        bldr.build_rev_with_config(
            cconfig, args.revision.strip(), additional_patches=patches
        )
    elif args.command == "patch":
        patcher = Patcher(
            Path(args.prefix),
            patchdb=patchdb,
            cores=args.jobs,
            builder=bldr,
        )
        if args.find_ranges:
            patcher.find_ranges(cconfig, args.revision.strip(), patches)
        elif args.find_introducer:
            patcher.find_introducer(cconfig, args.revision.strip())

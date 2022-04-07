import shlex
from argparse import ArgumentParser, Namespace
from multiprocessing import cpu_count
from subprocess import run
from pathlib import Path

from ccbuilder.builder.builder import (
    get_compiler_build_job,
    build_and_install_compiler,
    CompilerBuildJob,
)

from ccbuilder.utils.utils import get_compiler_config, CompilerConfig, Compiler
from ccbuilder.patcher.patchdatabase import PatchDB
from ccbuilder.patcher.patcher import Patcher

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

    build = subparsers.add_parser("build")
    # add "build-releases" option here

    patch = subparsers.add_parser("patch")
    patch.add_argument(
        "revision", type=str, help="Which revision to patch/Broken revision"
    )
    patch.add_argument(
        "--patches",
        nargs="+",
        help="Which patch(es) to apply.",
        type=str,
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

    return parser.parse_args()


def run_as_module() -> None:
    args = _parse_args()
    _initialize(args)
    cconfig = get_compiler_config(args.compiler, args.repos_dir)
    if args.pull:
        cconfig.repo.pull()

    if args.command == "build":
        build_and_install_compiler(
            get_compiler_build_job(
                cconfig,
                args.revision,
                PatchDB(Path(args.patches_dir) / "patchdb.json"),
            ),
            args.prefix,
            args.jobs,
        )
    elif args.command == "patch":
        patcher = Patcher(
            args.prefix, PatchDB(Path(args.patches_dir) / "patchdb.json"), args.jobs
        )
        if args.find_ranges:
            patcher.find_ranges(cconfig, args.revision, args.patches)
        elif args.find_introducer:
            patcher.find_introducer(cconfig, args.revision)

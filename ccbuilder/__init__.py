import logging
import os
import shlex
from argparse import ArgumentParser, Namespace
from multiprocessing import cpu_count
from pathlib import Path
from subprocess import run

from ccbuilder.builder.builder import (
    build_and_install_compiler,
    BuilderWithoutCache,
    Builder,
    BuildException,
)
from ccbuilder.patcher.patchdatabase import PatchDB
from ccbuilder.patcher.patcher import Patcher
from ccbuilder.utils.repository import Repo, RepositoryException, Revision, Commit
from ccbuilder.utils.utils import (
    CompilerProject,
    MajorCompilerReleases,
    CompilerReleases,
    get_repo,
    get_compiler_info,
    get_compiler_project,
    find_cached_revisions,
)

__all__ = [
    "CompilerProject",
    "Repo",
    "RepositoryException",
    "Revision",
    "Commit",
    "PatchDB",
    "Patcher",
    "build_and_install_compiler",
    "BuilderWithoutCache",
    "Builder",
    "BuildException",
    "CompilerReleases",
    "MajorCompilerReleases",
    "get_repo",
    "find_cached_revisions",
    "get_compiler_info",
    "get_compiler_project",
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


def ccbuilder_base_parser() -> ArgumentParser:
    parser = ArgumentParser("ccbuilder", add_help=False)

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
        "--cache-prefix",
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
        "--patches",
        nargs="+",
        help="For the 'patcher', this defines which patch(es) to apply. For the 'builder', \
            these are the additional patches to apply to the already existing patches from the PatchDB.",
        type=str,
    )

    return parser


def ccbuilder_build_parser() -> ArgumentParser:
    parser = ArgumentParser("build", add_help=False)
    parser.add_argument(
        "compiler", choices=["llvm", "gcc"], help="Which compiler to build and install"
    )
    parser.add_argument("revision", type=str, help="Target revision")
    return parser


def ccbuilder_patch_parser() -> ArgumentParser:
    parser = ArgumentParser("patch", add_help=False)
    parser.add_argument(
        "compiler", choices=["llvm", "gcc"], help="Which compiler to find patches for"
    )
    parser.add_argument(
        "revision", type=str, help="Which revision to patch/Broken revision"
    )

    patch_command = parser.add_mutually_exclusive_group(required=True)
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

    return parser


def ccbuilder_cache_parser() -> ArgumentParser:
    parser = ArgumentParser(add_help=False)
    parser.add_argument(
        "action",
        choices=("clean", "stats"),
        type=str,
        help="What you want to do with the cache. Clean will search and remove all unfinished cache entries. `stats` will print some statistics about the cache.",
    )
    return parser


def ccbuilder_parser() -> ArgumentParser:
    """Get the parsers of ccbuilder. Will return you the parent parser
    and the subparser.

    Args:

    Returns:
        Tuple[ArgumentParser, _SubParsersAction[ArgumentParser]]:
    """
    parser = ArgumentParser("ccbuilder", parents=[ccbuilder_base_parser()])
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("build", parents=[ccbuilder_build_parser()])
    subparsers.add_parser("patch", parents=[ccbuilder_patch_parser()])
    subparsers.add_parser("cache", parents=[ccbuilder_cache_parser()])

    return parser


def handle_pull(args: Namespace) -> bool:
    if args.pull:
        gcc_repo = get_repo(CompilerProject.GCC, Path(args.repos_dir) / "gcc")
        llvm_repo = get_repo(
            CompilerProject.LLVM, Path(args.repos_dir) / "llvm-project"
        )
        gcc_repo.pull()
        llvm_repo.pull()
        return True
    return False


def handle_build(args: Namespace, bldr: BuilderWithoutCache) -> bool:
    # TODO: handle separate repo inputs?
    patches = [Path(p.strip()).absolute() for p in args.patches] if args.patches else []
    if args.command == "build":
        project, _ = get_compiler_info(args.compiler, Path(args.repos_dir))
        print(bldr.build(project, args.revision.strip(), additional_patches=patches))
        return True
    return False


def handle_patch(args: Namespace, bldr: Builder, patchdb: PatchDB) -> bool:
    if args.command == "patch":
        patches = (
            [Path(p.strip()).absolute() for p in args.patches] if args.patches else []
        )
        project, repo = get_compiler_info(args.compiler, Path(args.repos_dir))
        patcher = Patcher(
            Path(args.cache_prefix),
            patchdb=patchdb,
            cores=args.jobs,
            builder=bldr,
        )
        if args.find_ranges:
            patcher.find_ranges(project, repo, args.revision.strip(), patches)
        elif args.find_introducer:
            patcher.find_introducer(project, repo, args.revision.strip())

        return True
    return False


def handle_cache(args: Namespace, cache_prefix: Path) -> bool:
    if args.command == "cache":
        if args.action == "clean":
            print("Cleaning...")
            for c in cache_prefix.iterdir():
                if c == (cache_prefix / "logs"):
                    continue
                if not (c / "DONE").exists():
                    try:
                        os.rmdir(c)
                    except FileNotFoundError:
                        print(c, "spooky. It just disappeared...")
                    except OSError:
                        print(c, "is not empty but also not done!")
            print("Done")
        elif args.action == "stats":
            count_gcc = 0
            count_clang = 0
            for c in cache_prefix.iterdir():
                if c.name.startswith("llvm"):
                    count_clang += 1
                else:
                    count_gcc += 1

            tot = count_gcc + count_clang
            print("Amount compilers:", tot)
            print(
                "Amount clang: {} {:.2f}%".format(count_clang, count_clang / tot * 100)
            )
            print("Amount GCC: {} {:.2f}%".format(count_gcc, count_gcc / tot * 100))

        return True
    return False


def run_as_module() -> None:
    args = ccbuilder_parser().parse_args()
    if args.log_level is not None:
        try:
            num_lvl = getattr(logging, args.log_level.upper())
            logging.basicConfig(level=num_lvl)
        except AttributeError:
            print(f"No such log level {args.log_level.upper()}")
            exit(1)

    _initialize(args)
    patchdb = PatchDB(Path(args.patches_dir) / "patchdb.json")
    cache_prefix = Path(args.cache_prefix.strip())

    if handle_pull(args):
        return
    if handle_cache(args, cache_prefix):
        return
    repo_dir_prefix = Path(args.repos_dir)
    llvm_repo = get_repo(CompilerProject.LLVM, repo_dir_prefix / "llvm-project")
    gcc_repo = get_repo(CompilerProject.LLVM, repo_dir_prefix / "gcc")

    bldr = Builder(
        Path(args.cache_prefix.strip()),
        gcc_repo=gcc_repo,
        llvm_repo=llvm_repo,
        patchdb=patchdb,
        jobs=args.jobs,
    )
    if handle_build(args, bldr):
        return
    if handle_patch(args, bldr, patchdb):
        return
    if handle_cache(args, cache_prefix):
        return

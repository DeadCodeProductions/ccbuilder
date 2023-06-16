import logging
import os
from argparse import ArgumentParser, Namespace
from multiprocessing import cpu_count
from pathlib import Path
from typing import Optional

from diopter.repository import (
    Repo,
    RepositoryException,
    Revision,
    Commit,
    get_gcc_repo,
    get_llvm_repo,
    get_gcc_releases,
    get_llvm_releases,
    DEFAULT_REPOS_DIR,
)
from diopter.compiler import CompilerProject

from ccbuilder.builder.builder import (
    build_and_install_compiler,
    Builder,
    BuildException,
)
from ccbuilder.utils.utils import (
    get_compiler_info,
    get_compiler_project,
    initialize_repos,
    initialize_patches_dir,
)
from ccbuilder.compilerstore import (
    CompilerStore,
    scan_directory_and_populate_store,
    load_compiler_store,
    default_store_file,
)
from ccbuilder.defaults import (
    DEFAULT_PREFIX_PARENT_PATH,
    DEFAULT_PATCH_DIR,
)

__all__ = [
    "CompilerProject",
    "Repo",
    "RepositoryException",
    "Revision",
    "Commit",
    "build_and_install_compiler",
    "BuilderWithoutCache",
    "Builder",
    "BuildException",
    "CompilerReleases",
    "MajorCompilerReleases",
    "get_gcc_repo",
    "get_llvm_repo",
    "get_compiler_info",
    "get_compiler_project",
    "initialize_repos",
    "initialize_patches_dir",
    "DEFAULT_PREFIX_PARENT_PATH",
    "DEFAULT_PATCH_DIR",
    "DEFAULT_REPOS_DIR",
]


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

    # XXX:the word prefix is a bit misleading, this is the parent of the prefix
    # Misleading in the way, that it could be mistaken for the installation prefix?
    parser.add_argument(
        "--cache-prefix",
        type=str,
        default=str(DEFAULT_PREFIX_PARENT_PATH),
        help=f"Installation prefix (default: {DEFAULT_PREFIX_PARENT_PATH})",
    )
    default_patch_dir = str()
    parser.add_argument(
        "--patches-dir",
        type=str,
        default=str(DEFAULT_PATCH_DIR),
        help="Path to the patches directory (the patches should be in two "
        f"subdirectories, /gcc and /llvm) (default: {DEFAULT_PATCH_DIR})",
    )
    parser.add_argument(
        "--repos-dir",
        type=str,
        default=str(DEFAULT_REPOS_DIR),
        help=f"Path to the directory with the compiler repositories (default: {DEFAULT_REPOS_DIR})",
    )

    parser.add_argument(
        "--logdir",
        help="Path to the directory where log files should be stored in. If not specified, ccbuilder will print to stdout.",
        type=str,
    )

    parser.add_argument(
        "--print-releases",
        action="store_true",
        default=False,
        help="Prints the available releases for GCC and LLVM",
    )

    parser.add_argument(
        "--print-failed",
        action="store_true",
        default=False,
        help="Prints the failed to build commits for GCC and LLVM",
    )

    parser.add_argument(
        "--clear-failed",
        action="store_true",
        default=False,
        help="Clears all failed to build history",
    )

    return parser


def ccbuilder_build_parser() -> ArgumentParser:
    parser = ArgumentParser("build", add_help=False)
    parser.add_argument(
        "compiler", choices=["llvm", "gcc"], help="Which compiler to build and install"
    )
    parser.add_argument("revision", type=str, help="Target revision")
    parser.add_argument(
        "--additional-configure-flags",
        type=str,
        help="Additional flags to pass to configure/cmake",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force rebuild a compiler even if it failed previously building",
    )
    return parser


def ccbuilder_cache_parser() -> ArgumentParser:
    parser = ArgumentParser(add_help=False)
    parser.add_argument(
        "action",
        choices=("clean", "stats", "scan"),
        type=str,
        help="What you want to do with the cache. "
        "Clean will search and remove all unfinished cache entries. "
        "`stats` will print some statistics about the cache."
        "`scan` will scan the given directory for compilers and (re-)compute "
        "the necesary metadata required for retrieving compilers, bisecting, etc.",
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
    subparsers.add_parser("cache", parents=[ccbuilder_cache_parser()])

    return parser


def handle_pull(args: Namespace) -> bool:
    if args.pull:
        gcc_repo = get_gcc_repo(Path(args.repos_dir) / "gcc")
        llvm_repo = get_llvm_repo(Path(args.repos_dir) / "llvm-project")
        gcc_repo.pull()
        llvm_repo.pull()
        return True
    return False


def handle_print_releases(args: Namespace) -> bool:
    if args.print_releases:
        print("GCC releases:")
        for r in get_gcc_releases(get_gcc_repo(Path(args.repos_dir) / "gcc")):
            print(r)

        print("LLVM releases:")
        for r in get_llvm_releases(
            get_llvm_repo(Path(args.repos_dir) / "llvm-project")
        ):
            print(r)

        return True
    return False


def handle_print_failed(args: Namespace, cstore: CompilerStore) -> bool:
    if not args.print_failed:
        return False
    for comp, commit in cstore.failed_to_build_compilers():
        print(f"{comp}: {commit}")
    return True


def handle_clear_failed(args: Namespace, cstore: CompilerStore) -> bool:
    if not args.clear_failed:
        return False
    cstore.clear_previously_failed_to_build()
    return True


def handle_build(args: Namespace, bldr: Builder) -> bool:
    # TODO: handle separate repo inputs?
    if args.command == "build":
        project, _ = get_compiler_info(args.compiler, Path(args.repos_dir))
        try:
            print(
                bldr.build(
                    project,
                    args.revision.strip(),
                    configure_flags=args.additional_configure_flags,
                    force=args.force is True,
                ).prefix
            )
        except BuildException as e:
            print(e)
        return True
    return False


def handle_cache(args: Namespace, cache_prefix: Path) -> bool:
    if args.command != "cache":
        return False
    match args.action:
        case "clean":
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
        case "stats":
            store_filename = default_store_file(cache_prefix)
            if not store_filename.exists():
                print(
                    "No compiler store metadata found. Run `ccbuilder cache scan` first."
                )
                exit(0)
            store = load_compiler_store(store_filename)
            count_clang = len(store.built_commits(CompilerProject.LLVM))
            count_gcc = len(store.built_commits(CompilerProject.GCC))
            tot = count_gcc + count_clang
            print("Amount compilers:", tot)
            print(f"Amount LLVM: {count_clang} {count_clang / tot :.2%}")
            print(f"Amount GCC: {count_gcc} {count_gcc / tot:.2%}")
        case "scan":
            print("Scanning...")
            store = load_compiler_store(default_store_file(cache_prefix))
            scan_directory_and_populate_store(cache_prefix, store)
            print("Done")
            print(
                f"{len(store.built_commits(CompilerProject.GCC))} gcc compilers found"
            )
            print(
                f"{len(store.built_commits(CompilerProject.LLVM))} llvm compilers found"
            )

    return True


def run_as_module() -> None:
    args = ccbuilder_parser().parse_args()
    if args.log_level is not None:
        try:
            num_lvl = getattr(logging, args.log_level.upper())
            logging.basicConfig(level=num_lvl)
        except AttributeError:
            print(f"No such log level {args.log_level.upper()}")
            exit(1)

    # TODO: call the next two functions automatically when importing ccbuilder?
    initialize_repos(args.repos_dir)
    initialize_patches_dir(args.patches_dir)

    cache_prefix = Path(args.cache_prefix.strip())

    if handle_print_releases(args):
        return
    store = load_compiler_store(default_store_file(cache_prefix))
    if handle_print_failed(args, store):
        return
    if handle_clear_failed(args, store):
        return
    if handle_pull(args):
        return
    if handle_cache(args, cache_prefix):
        return
    repo_dir_prefix = Path(args.repos_dir)
    llvm_repo = get_llvm_repo(repo_dir_prefix / "llvm-project")
    gcc_repo = get_gcc_repo(repo_dir_prefix / "gcc")

    if args.logdir:
        logdir = Path(args.logdir).absolute()
        logdir.mkdir(exist_ok=True, parents=True)
    else:
        logdir = None

    bldr = Builder(
        Path(args.cache_prefix.strip()).absolute(),
        gcc_repo=gcc_repo,
        llvm_repo=llvm_repo,
        cstore=store,
        patches_dir=Path(args.patches_dir),
        logdir=logdir,
        jobs=args.jobs,
    )

    if handle_build(args, bldr):
        return
    if handle_cache(args, cache_prefix):
        return

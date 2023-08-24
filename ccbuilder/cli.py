from argparse import ArgumentParser, Namespace, Action
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, Type, TypeVar, Literal, cast
from multiprocessing import cpu_count

from diopter.repository import Revision

from ccbuilder.defaults import (
    DEFAULT_COMPILER_STORE_DIR,
    DEFAULT_PATCH_DIR,
    DEFAULT_REPOS_DIR,
)


__all__ = ["parse_args", "CompilerStoreActionOptions", "BuildActionOptions", "Options"]


class CompilerStoreActionOptions(Enum):
    print_failed = 1
    clear_failed_from_history = 2
    clean_unfinished_builds_from_store = 3
    print_stats = 4
    scan = 5


@dataclass(frozen=True, kw_only=True)
class BuildActionOptions:
    compiler: Literal["gcc", "llvm", "clang"]
    revision: Revision
    additional_configure_flags: str
    force_rebuild: bool


@dataclass(frozen=True, kw_only=True)
class Options:
    log_level: str | None
    pull: bool
    print_releases: bool
    jobs: int
    store_path: Path
    patches_dir: Path
    repos_dir: Path
    logdir: Path | None
    action: CompilerStoreActionOptions | BuildActionOptions | None


class StoreAndEraseAction(Action):
    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        erase_dest: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.erase_dest = erase_dest
        super(StoreAndEraseAction, self).__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        if self.erase_dest:
            setattr(namespace, self.erase_dest, None)


class BooleanStoreAndEraseAction(Action):
    def __init__(
        self,
        option_strings: list[str],
        dest: str,
        erase_dest: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.erase_dest = erase_dest
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, True)
        if self.erase_dest:
            setattr(namespace, self.erase_dest, None)


def add_common_arguments(parser: ArgumentParser, is_main_parser: bool) -> None:
    parser.add_argument(
        "-ll",
        "--log-level",
        action=StoreAndEraseAction,
        erase_dest="ll" if not is_main_parser else None,
        type=str,
        choices=("debug", "info", "warning", "error", "critical"),
        dest="ll" if is_main_parser else "sub_ll",
        help="Log level",
    )

    parser.add_argument(
        "--pull",
        action=BooleanStoreAndEraseAction,
        erase_dest="pull" if not is_main_parser else None,
        default=False if is_main_parser else None,
        dest="pull" if is_main_parser else "sub_pull",
        help="Update the GCC and LLVM repositories",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        action=StoreAndEraseAction,
        erase_dest="jobs" if not is_main_parser else None,
        default=cpu_count() if is_main_parser else None,
        dest="jobs" if is_main_parser else "sub_jobs",
        metavar="JOBS",
        help="Number of build jobs (default:use all cores)",
    )

    parser.add_argument(
        "--compiler-store-path",
        type=Path,
        action=StoreAndEraseAction,
        erase_dest="compiler_store_path" if not is_main_parser else None,
        default=str(DEFAULT_COMPILER_STORE_DIR) if is_main_parser else None,
        dest="compiler_store_path" if is_main_parser else "sub_compiler_store_path",
        metavar="PATH",
        help=f"Built compilers are stored under this directory (default: {DEFAULT_COMPILER_STORE_DIR})",
    )
    parser.add_argument(
        "--patches-dir",
        type=Path,
        action=StoreAndEraseAction,
        erase_dest="patches_dir" if not is_main_parser else None,
        default=str(DEFAULT_PATCH_DIR) if is_main_parser else None,
        dest="patches_dir" if is_main_parser else "sub_patches_dir",
        metavar="PATH",
        help="Path to the patches directory (the patches should be in two "
        f"subdirectories, /gcc and /llvm) (default: {DEFAULT_PATCH_DIR})",
    )
    parser.add_argument(
        "--repos-dir",
        type=Path,
        action=StoreAndEraseAction,
        erase_dest="repos_dir" if not is_main_parser else None,
        default=str(DEFAULT_REPOS_DIR) if is_main_parser else None,
        dest="repos_dir" if is_main_parser else "sub_repos_dir",
        metavar="PATH",
        help=f"Path to the directory with the compiler repositories (default: {DEFAULT_REPOS_DIR})",
    )

    parser.add_argument(
        "--logdir",
        type=Path,
        action=StoreAndEraseAction,
        erase_dest="logdir" if not is_main_parser else None,
        metavar="PATH",
        help="Path to the directory where log files should be stored in. If not specified, ccbuilder will print to stdout.",
    )

    parser.add_argument(
        "--print-releases",
        action=BooleanStoreAndEraseAction,
        erase_dest="print_releases" if not is_main_parser else None,
        default=False if is_main_parser else None,
        dest="print_releases" if is_main_parser else "sub_print_releases",
        help="Prints the available releases for GCC and LLVM",
    )


def populate_compiler_store_parser(store_parser: ArgumentParser) -> None:
    store_parser.add_argument(
        "action",
        choices=("clear-unfinished", "stats", "scan", "print-failed", "clear-failed"),
        type=str,
        help="What you want to do with the compiler store. "
        "`clear-unfinished` will search and remove all unfinished compiler store entries. "
        "`stats` will print some statistics about the compiler store."
        "`scan` will scan the given directory for compilers and (re-)compute "
        "the necesary metadata required for retrieving compilers, bisecting, etc."
        "`clear-failed` will clear all failed to build history."
        "`print-failed` prints all revisions that failed building.",
    )
    add_common_arguments(store_parser, is_main_parser=False)


def populate_build_parser(build_parser: ArgumentParser) -> None:
    build_parser.add_argument(
        "compiler", choices=["llvm", "gcc"], help="Which compiler to build and install"
    )
    build_parser.add_argument("revision", type=str, help="Target revision")
    build_parser.add_argument(
        "--additional-configure-flags",
        type=str,
        help="Additional flags to pass to configure/cmake",
    )
    build_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force rebuild a compiler even if it failed previously building",
    )
    add_common_arguments(build_parser, is_main_parser=False)


def parse_main_and_sub_args(
    mixed_args: Namespace,
) -> dict[str, str | int | bool | Path]:
    args = vars(mixed_args)
    main_args = {}
    sub_args = {}
    for arg, value in args.items():
        if arg.startswith("sub_"):
            sub_args[arg[4:]] = value
        else:
            main_args[arg] = value
    parsed_args = {}
    for arg_name in sub_args.keys() | main_args.keys():
        v1 = sub_args.get(arg_name, None)
        v2 = main_args.get(arg_name, None)
        assert v1 is None or v2 is None, f"Argument {arg_name} is specified twice"
        if v1 is not None:
            parsed_args[arg_name] = v1
        elif v2 is not None:
            parsed_args[arg_name] = v2
    return parsed_args


T = TypeVar("T")


def checked_cast(value: Any, type_: Type[T]) -> T:
    assert isinstance(value, type_), f"Expected type {type_}, got {type(value)}"
    return value


def parse_args() -> Options:
    parser = ArgumentParser("ccbuilder")
    add_common_arguments(parser, is_main_parser=True)
    subparsers = parser.add_subparsers(dest="command")
    populate_compiler_store_parser(subparsers.add_parser("store"))
    populate_build_parser(subparsers.add_parser("build"))
    args = parse_main_and_sub_args(parser.parse_args())

    action: CompilerStoreActionOptions | BuildActionOptions | None
    if "command" not in args:
        action = None
    else:
        match args["command"]:
            case "store":
                action = CompilerStoreActionOptions(
                    {
                        "clear-unfinished": CompilerStoreActionOptions.clean_unfinished_builds_from_store,
                        "stats": CompilerStoreActionOptions.print_stats,
                        "scan": CompilerStoreActionOptions.scan,
                        "clear-failed": CompilerStoreActionOptions.clear_failed_from_history,
                    }[checked_cast(args["action"], str)]
                )
            case "build":
                assert args["compiler"] in ("llvm", "gcc", "clang")
                action = BuildActionOptions(
                    compiler=cast(Literal["llvm", "gcc", "clang"], args["compiler"]),
                    revision=checked_cast(args["revision"], Revision),
                    additional_configure_flags=checked_cast(
                        args.get("additional_configure_flags", ""), str
                    ),
                    force_rebuild=checked_cast(args["force"], bool),
                )

    log_level: str | None = (
        checked_cast(args["log_level"], str) if "log_level" in args else None
    )
    pull = checked_cast(args["pull"], bool)
    print_releases = checked_cast(args["print_releases"], bool)
    jobs = checked_cast(args["jobs"], int)
    store_path = checked_cast(args["compiler_store_path"], Path)
    patches_dir = checked_cast(args["patches_dir"], Path)
    repos_dir = checked_cast(args["repos_dir"], Path)
    logdir = checked_cast(args["logdir"], Path) if "logdir" in args else None
    return Options(
        log_level=log_level,
        pull=pull,
        print_releases=print_releases,
        jobs=jobs,
        store_path=store_path,
        patches_dir=patches_dir,
        repos_dir=repos_dir,
        logdir=logdir,
        action=action,
    )

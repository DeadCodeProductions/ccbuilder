import logging
import os

from diopter.repository import (
    Repo,
    Commit,
    get_gcc_repo,
    get_llvm_repo,
    get_gcc_releases,
    get_llvm_releases,
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
    DEFAULT_COMPILER_STORE_DIR,
    DEFAULT_PATCH_DIR,
    DEFAULT_REPOS_DIR,
)
from ccbuilder.cli import (
    Options,
    parse_args,
    BuildActionOptions,
    CompilerStoreActionOptions,
)

__all__ = [
    "CompilerProject",
    "Repo",
    "Commit",
    "build_and_install_compiler",
    "Builder",
    "BuildException",
    "get_gcc_repo",
    "get_llvm_repo",
    "get_compiler_info",
    "get_compiler_project",
    "default_store_file",
    "load_compiler_store",
    "initialize_repos",
    "initialize_patches_dir",
    "DEFAULT_COMPILER_STORE_DIR",
    "DEFAULT_PATCH_DIR",
    "DEFAULT_REPOS_DIR",
]


def handle_pull(opts: Options) -> bool:
    if opts.pull:
        gcc_repo = get_gcc_repo(opts.repos_dir / "gcc")
        llvm_repo = get_llvm_repo(opts.repos_dir / "llvm-project")
        gcc_repo.pull()
        llvm_repo.pull()
        return True
    return False


def handle_print_releases(opts: Options) -> bool:
    if opts.print_releases:
        print("GCC releases:")
        for r in get_gcc_releases(get_gcc_repo(opts.repos_dir / "gcc")):
            print(r)

        print("LLVM releases:")
        for r in get_llvm_releases(get_llvm_repo(opts.repos_dir / "llvm-project")):
            print(r)

        return True
    return False


def handle_build_action(opts: Options) -> bool:
    if not isinstance(opts.action, BuildActionOptions):
        return False

    cstore = load_compiler_store_from_opts(opts)
    repo_dir_prefix = opts.repos_dir
    llvm_repo = get_llvm_repo(repo_dir_prefix / "llvm-project")
    gcc_repo = get_gcc_repo(repo_dir_prefix / "gcc")

    bldr = Builder(
        opts.store_path,
        gcc_repo=gcc_repo,
        llvm_repo=llvm_repo,
        cstore=cstore,
        patches_dir=opts.patches_dir,
        logdir=opts.logdir,
        jobs=os.cpu_count(),
    )

    project, _ = get_compiler_info(opts.action.compiler, opts.repos_dir)
    try:
        print(
            bldr.build(
                project,
                opts.action.revision,
                configure_flags=opts.action.additional_configure_flags,
                force=opts.action.force_rebuild,
            ).prefix
        )
    except BuildException as e:
        print(e)
    return True


def scan(opts: Options) -> None:
    print("Scanning for built compilers...")
    store = load_compiler_store(default_store_file(opts.store_path))
    scan_directory_and_populate_store(opts.store_path, store)
    print("Done")
    print(f"{len(store.built_commits(CompilerProject.GCC))} gcc compilers found")
    print(f"{len(store.built_commits(CompilerProject.LLVM))} llvm compilers found")


def load_compiler_store_from_opts(opts: Options) -> CompilerStore:
    store_filename = default_store_file(opts.store_path)
    if not store_filename.exists():
        print(f"No compiler store metadata found in {opts.store_path}.")
        scan(opts)
    return load_compiler_store(store_filename)


def handle_cstore_action(opts: Options) -> bool:
    if not isinstance(opts.action, CompilerStoreActionOptions):
        return False
    store_path = opts.store_path
    match opts.action:
        case CompilerStoreActionOptions.clean_unfinished_builds_from_store:
            print("Cleaning unfinished builds...")
            for c in store_path.iterdir():
                if not c.is_dir():
                    continue
                if c == (store_path / "logs"):
                    continue
                if not (c / "DONE").exists():
                    try:
                        os.rmdir(c)
                    except FileNotFoundError:
                        print(c, " spooky. It just disappeared...")
                    except OSError:
                        print(c, " is not empty but also not done!")
            print("Done")
        case CompilerStoreActionOptions.print_stats:
            store = load_compiler_store_from_opts(opts)
            count_clang = len(store.built_commits(CompilerProject.LLVM))
            count_gcc = len(store.built_commits(CompilerProject.GCC))
            tot = count_gcc + count_clang
            print("Amount compilers:", tot)
            print(f"Amount LLVM: {count_clang} {count_clang / tot :.2%}")
            print(f"Amount GCC: {count_gcc} {count_gcc / tot:.2%}")
        case CompilerStoreActionOptions.scan:
            scan(opts)
        case CompilerStoreActionOptions.clear_failed_from_history:
            store = load_compiler_store_from_opts(opts)
            store.clear_previously_failed_to_build()
        case CompilerStoreActionOptions.print_failed:
            store = load_compiler_store_from_opts(opts)
            for comp, commit in store.failed_to_build_compilers():
                print(f"{comp}: {commit}")

    return True


def run_as_module() -> None:
    opts = parse_args()
    if opts.log_level is not None:
        try:
            num_lvl = getattr(logging, opts.log_level.upper())
            logging.basicConfig(level=num_lvl)
        except AttributeError:
            print(f"No such log level {opts.log_level.upper()}")
            exit(1)
    if opts.logdir:
        logdir = opts.logdir.absolute()
        logdir.mkdir(exist_ok=True, parents=True)
    else:
        logdir = None

    initialize_repos(opts.repos_dir)
    initialize_patches_dir(opts.patches_dir)

    if handle_print_releases(opts):
        return
    if handle_pull(opts):
        return
    if handle_cstore_action(opts):
        return
    if handle_build_action(opts):
        return

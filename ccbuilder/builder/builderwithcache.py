import logging
import time
from pathlib import Path
from typing import Optional

from ccbuilder.builder.builder import (
    Builder,
    BuildException,
    CompilerBuildJob,
    get_install_path_from_job,
)
from ccbuilder.patcher.patchdatabase import PatchDB


class BuilderWithCache(Builder):
    def __init__(
        self, cache_prefix: Path, patchdb: PatchDB, cores: Optional[int] = None
    ):
        super().__init__(cache_prefix, patchdb, cores)

    def build_job(
        self, job: CompilerBuildJob, additional_patches: list[Path] = []
    ) -> Path:

        install_path = get_install_path_from_job(job, self.prefix)
        success_indicator = install_path / "DONE"
        if success_indicator.exists():
            return install_path
        elif install_path.exists():
            logging.info(
                f"This compiler seems to be built currently by some other worker. Need to wait. If there is no other worker, abort this command and run ccbuilder cache clean."
            )
            start_time = time.time()
            counter = 0
            while not success_indicator.exists():
                time.sleep(1)
                if time.time() - start_time > 15 * 60:
                    counter += 1
                    logging.info(
                        f"{counter*15} minutes have passed waiting. Maybe the cache is in an inconsistent state."
                    )
                    start_time = time.time()
                if not install_path.exists():
                    raise BuildException(
                        f"Other build attempt failed for {install_path}"
                    )
            return install_path

        return super().build_job(job, additional_patches=additional_patches)
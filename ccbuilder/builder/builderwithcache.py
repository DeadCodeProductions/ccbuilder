import logging
import os
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


def worker_alive(worker_indicator: Path) -> bool:
    if worker_indicator.exists():
        with open(worker_indicator, "r") as f:
            worker_pid = int(f.read())
        try:
            # Signal to ~check if a process is alive
            # from man 1 kill
            # > If signal is 0, then no actual signal is sent, but error checking is still performed.
            os.kill(worker_pid, 0)
            return True
        except OSError:
            return False
    return False


class BuilderWithCache(Builder):
    def build_job(
        self, job: CompilerBuildJob, additional_patches: list[Path] = []
    ) -> Path:

        install_path = get_install_path_from_job(job, self.cache_prefix)
        success_indicator = install_path / "DONE"
        worker_indicator = install_path / "WORKER_PID"
        if success_indicator.exists():
            return install_path
        elif install_path.exists() and worker_alive(worker_indicator):

            logging.info(
                f"This compiler seems to be built by some other worker. Need to wait. If there is no other worker, abort this command and run ccbuilder cache clean."
            )
            start_time = time.time()
            counter = 0
            while (
                worker_alive(worker_indicator)
                and not success_indicator.exists()
                and install_path.exists()
            ):
                time.sleep(1)
                if time.time() - start_time > 15 * 60:
                    counter += 1
                    logging.info(
                        f"{counter*15} minutes have passed waiting. Maybe the cache is in an inconsistent state."
                    )
                    start_time = time.time()

            if success_indicator.exists():
                return install_path

            # Someone was building but did not leave the success_indicator
            # so something went wrong with the build.
            raise BuildException(f"Other build attempt failed for {install_path}")

        return super().build_job(job, additional_patches=additional_patches)

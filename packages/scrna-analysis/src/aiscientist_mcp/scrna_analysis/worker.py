"""An admitted analysis task, executed inside its Slurm allocation or local process."""

import argparse
import contextlib
import os
import sys

from aiscientist_mcp.common.jobs import record_done

from .runtime import _task_dir, execute, workspace


def _configure_caches(task):
    # A clean read-only SIF has no writable home. Cache writes belong to this
    # attempt, never a global /tmp directory another user/job may have created.
    for variable, name in (("NUMBA_CACHE_DIR", "numba-cache"), ("MPLCONFIGDIR", "matplotlib-cache")):
        cache = task / name
        if cache.is_symlink() or cache.resolve().parent != task.resolve():
            raise ValueError("worker cache must stay inside its private task")
        cache.mkdir(exist_ok=True)
        os.environ[variable] = str(cache)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-id", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    task = _task_dir(workspace(args.analysis_id), args.task_id)

    def run():
        _configure_caches(task)
        execute(args.analysis_id, args.task_id)

    # The frozen scientific interpreter deliberately has no MCP transport deps.
    with contextlib.redirect_stdout(sys.stderr):
        record_done(task, run)


if __name__ == "__main__":
    main()

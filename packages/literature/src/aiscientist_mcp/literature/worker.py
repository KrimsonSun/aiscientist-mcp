"""Run one admitted literature task, without credentials in its command or request file."""

import argparse
import contextlib
import sys

from aiscientist_mcp.common.jobs import record_done

from .runtime import execute, task_dir, workspace
from .settings import ModelSettings


class _Redacted:
    def __init__(self, stream, key):
        self.stream, self.key = stream, key

    def write(self, text):
        return self.stream.write(text.replace(self.key, "[REDACTED]") if self.key else text)

    def flush(self):
        self.stream.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-id", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    task = task_dir(workspace(args.analysis_id), args.task_id)
    try:
        key = ModelSettings.from_env().api_key()
    except ValueError:
        key = ""
    stream = _Redacted(sys.stderr, key)

    def run():
        try:
            execute(args.analysis_id, args.task_id)
        except Exception as exc:
            message = str(exc).replace(key, "[REDACTED]") if key else str(exc)
            raise ValueError(message) from None

    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        record_done(task, run)


if __name__ == "__main__":
    main()

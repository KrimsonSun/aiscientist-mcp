"""One isolated common.tasks worker; credentials remain in the environment."""

from aiscientist_mcp.common.tasks import worker_main

from .runtime import CONFIG, safe_execute


def main():
    worker_main(CONFIG, safe_execute)


if __name__ == "__main__":
    main()

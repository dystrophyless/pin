from pin.bootstrap.config import ProcessRole
from pin.entrypoints._common import run


def main() -> None:
    run(ProcessRole.APP_WORKER)


if __name__ == "__main__":
    main()


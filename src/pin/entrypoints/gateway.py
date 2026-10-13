from pin.bootstrap.config import ProcessRole
from pin.entrypoints._common import run


def main() -> None:
    run(ProcessRole.GATEWAY)


if __name__ == "__main__":
    main()


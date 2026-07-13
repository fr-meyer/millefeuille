"""Allow ``python -m millefeuille`` invocation."""

import sys

from millefeuille.cli.main import entrypoint

if __name__ == "__main__":
    sys.exit(entrypoint())

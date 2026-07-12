"""Allow ``python -m millefeuille`` invocation."""

import sys

from millefeuille.cli.main import main

if __name__ == "__main__":
    sys.exit(main())

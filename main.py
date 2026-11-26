"""Start the bot: ``python main.py [--config config.toml] [--check]``.

Same as ``python -m mangosbot``. See README.md for setup.
"""

import sys

from mangosbot.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

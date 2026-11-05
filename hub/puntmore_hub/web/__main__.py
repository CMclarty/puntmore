"""``python -m puntmore_hub.web [--bind host:port]``: the same as ``puntmore web``."""
import sys

from ..cli import main

if __name__ == "__main__":
    main(["web", *sys.argv[1:]])

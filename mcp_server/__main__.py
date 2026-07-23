"""`python -m mcp_server` entrypoint."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mcp_server.server import main  # noqa: E402

main()

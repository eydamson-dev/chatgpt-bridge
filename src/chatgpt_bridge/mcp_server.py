from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from chatgpt_bridge import __version__

mcp = MCPServer("chatgpt-bridge", version=__version__)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

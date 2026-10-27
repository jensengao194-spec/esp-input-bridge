"""Three compact MCP tools; all reasoning stays in the calling agent."""
from __future__ import annotations

import argparse
import base64
import json
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from .adapter import InputAdapter, load_adapter
from .models import Action


def create_mcp(adapter: InputAdapter):
    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await adapter.close()

    server = FastMCP("esp-input-bridge", lifespan=lifespan)

    def result(with_image):
        content = [TextContent(type="text", text=json.dumps(adapter.summary(), separators=(",", ":")))]
        if with_image and adapter.latest:
            content.append(ImageContent(type="image", mimeType="image/jpeg",
                                        data=base64.b64encode(adapter.latest.jpeg()).decode()))
        return CallToolResult(content=content)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def observe(image: bool = True) -> CallToolResult:
        """Read target state and a fresh observation ID; optionally include a cropped image."""
        await adapter.observe()
        return result(image)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                             idempotentHint=False, openWorldHint=True))
    async def act(observation_id: str, actions: list[Action], image: bool = False) -> CallToolResult:
        """Execute up to 8 hardware input actions on a fresh observation. Return next state/image."""
        await adapter.act(observation_id, actions)
        return result(image)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    async def stop() -> dict:
        """Stop this session and release inputs; restart the server to permit inputs again."""
        return await adapter.stop()

    return server


def main(argv=None):
    p = argparse.ArgumentParser(description="ESP Input Bridge MCP over stdio")
    p.add_argument("--config")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args(argv)
    server = create_mcp(load_adapter(args.config, demo=args.demo))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""A minimal stdio MCP server used by test_mcp.py (run as a subprocess)."""

import asyncio

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool


async def handle_list_tools(ctx, params):
    return ListToolsResult(
        tools=[
            Tool(
                name="add",
                description="Add two numbers",
                inputSchema={
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                },
            ),
            Tool(
                name="echo",
                description="Echo the given text",
                inputSchema={"type": "object", "properties": {"text": {"type": "string"}}},
            ),
        ]
    )


async def handle_call_tool(ctx, params):
    name = params.name
    arguments = params.arguments or {}
    if name == "add":
        total = int(arguments.get("a", 0)) + int(arguments.get("b", 0))
        return CallToolResult(content=[TextContent(type="text", text=f"sum={total}")])
    if name == "echo":
        return CallToolResult(content=[TextContent(type="text", text=str(arguments.get("text", "")))])
    raise ValueError(f"unknown tool: {name}")


server = Server("test-server", on_list_tools=handle_list_tools, on_call_tool=handle_call_tool)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

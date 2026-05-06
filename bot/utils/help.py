"""Built-in help-text helpers used by the global error handlers and ``?cmds``.

Both functions look at the live bot instance, so they live next to the rest
of the runtime helpers rather than inside the help cog (which only renders
the embeds).
"""

from __future__ import annotations

from typing import Union

from bot.utils.state import get_bot


def get_command_syntax(command_name: str) -> str:
    """Render a one-line syntax hint like ``balance [member_name (str)]``."""
    bot = get_bot()
    command = bot.get_command(command_name)
    if not command:
        return f"Command `{command_name}` not found."

    syntax_parts = [f"**{command.name}**"]
    if command.aliases:
        syntax_parts[0] += (
            " (aliases: " + ", ".join(f"`{a}`" for a in command.aliases) + ")"
        )

    params: list[str] = []
    for param_name, param in command.clean_params.items():
        if param_name in ("ctx", "interaction"):
            continue

        if param.default is not param.empty:
            param_str = f"[{param_name}]"
        else:
            param_str = f"<{param_name}>"

        if param.annotation and param.annotation != param.empty:
            if hasattr(param.annotation, "__name__"):
                param_str += f" ({param.annotation.__name__})"
            elif hasattr(param.annotation, "__origin__"):
                if param.annotation.__origin__ is Union:
                    types = [
                        t.__name__
                        for t in param.annotation.__args__
                        if t is not type(None)
                    ]
                    param_str += f" ({'|'.join(types)})"

        params.append(param_str)

    if params:
        syntax_parts.append(" ".join(params))

    description = command.description or command.help
    if description:
        syntax_parts.append(f"\n*{description}*")

    return " ".join(syntax_parts)


def find_similar_commands(command_name: str, limit: int = 3) -> list[str]:
    """Cheap fuzzy match for ``CommandNotFound`` suggestions.

    Substring match in either direction, plus a 3-letter prefix fallback.
    Good enough for typos without pulling in a fuzzy-string library.
    """
    bot = get_bot()
    command_name = command_name.lower()
    similar: list[str] = []

    for cmd in bot.walk_commands():
        if cmd.name.lower() == command_name:
            continue

        cmd_names = [cmd.name.lower()] + [a.lower() for a in cmd.aliases]
        found = False
        for name in cmd_names:
            if command_name in name or name in command_name:
                similar.append(cmd.name)
                found = True
                break

        if not found and len(command_name) >= 3:
            for name in cmd_names:
                if command_name[:3] in name:
                    similar.append(cmd.name)
                    break

    return similar[:limit]


__all__ = ["get_command_syntax", "find_similar_commands"]

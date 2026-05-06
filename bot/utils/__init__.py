"""Cross-cutting helpers shared by cogs, events, and tasks.

Submodules are imported lazily by callers — there is intentionally no eager
re-export here so a cog only pays for what it needs.
"""

# PatosX, a multipurpose Discord bot (moderation, economy, AI, fun)
# Copyright (C) 2025 theofficialtruck
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""COPY-PASTE TEMPLATE ONLY - the extension loader in main.py skips files whose name starts
with an underscore, so this module is never loaded.

To add a feature domain: copy this file, drop the leading underscore, rename the class, and
delete whatever you do not need. The conventions every cog follows:

* ``__init__`` stores ``self.bot``. Loops the cog owns are started in ``on_ready`` (guarded so a
  reconnect cannot start them twice, exactly like the old single ``on_ready`` did) and cancelled
  in ``cog_unload`` - nothing else cancels them for you.
* Shared state is always read through the module (``state.xp_col``, ``state.session``), never via
  ``from core.state import xp_col``; see core/state.py for why. The same goes for the helper
  modules (``econ.get_user``, ``perms.check_channel``, ``xp.check_and_award_badges``).
* Commands are ``@commands.hybrid_command`` (or ``@commands.command``) methods decorated with the
  permission checks from core.permsHelperFuncs. ``@xp.xp_earn`` goes innermost.
* Per-command error handlers only special-case what the command cares about; anything else falls
  through to the global handlers wired up in main.py.
* Listeners are ``@commands.Cog.listener()`` methods. Discord fans one event out to every cog,
  so add them freely - but never call ``bot.process_commands`` here (main.py does that, once).
* A cog never imports another cog. If it truly needs one at runtime, use
  ``self.bot.get_cog("ClassName")`` inside the function body.
"""

import discord
from discord.ext import commands, tasks

from core import config as cfg
from core import economyHelperFuncs as econ
from core import permsHelperFuncs as perms
from core import state
from core import xpHelperFuncs as xp


class ExampleCog(commands.Cog):
    """One-line description of the feature domain this cog owns."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ready_done = False

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.hybrid_command(name="example", description="Show the invoker's wallet balance.")
    @perms.blacklist_barrier()
    @xp.xp_earn(1, 2)
    async def example(self, ctx: commands.Context):
        """Commands call into core/ for shared logic instead of reimplementing it."""
        if not await perms.check_channel(ctx, "economy_channel", "Economy"):
            return
        balance = await econ.get_balance(ctx.author.id, ctx.guild.id)
        await ctx.send(f"🪙 {ctx.author.display_name} has {balance} coins. ({cfg.BOT_ADMIN_NAME} says hi)")

    @example.error
    async def example_error(self, ctx: commands.Context, error: commands.CommandError):
        """Swallow only what this command wants to special-case; re-raise everything else so the
        global on_command_error in main.py still sees it."""
        if isinstance(error, commands.CheckFailure):
            return
        raise error

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        """Start owned loops once the bot is connected. Guarded because on_ready can fire again
        after a reconnect."""
        if self._ready_done:
            return
        self._ready_done = True
        if not self.example_loop.is_running():
            self.example_loop.start()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Side-effect-only listeners are safe to add; every cog listening for the event gets it."""
        await state.xp_col.find_one({"_id": f"{member.guild.id}-{member.id}"})

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    @tasks.loop(minutes=5)
    async def example_loop(self):
        for guild in self.bot.guilds:
            print(f"[ExampleCog] tick for {guild.name}")

    @example_loop.before_loop
    async def before_example_loop(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):
        """Cancel every loop this cog started."""
        if self.example_loop.is_running():
            self.example_loop.cancel()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExampleCog(bot))

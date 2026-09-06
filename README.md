# PatosX

PatosX is a multipurpose Discord bot built for moderation, economy gameplay, AI-powered interactions (DuckGPT), and fun/utility commands.
[![CI](https://github.com/theofficialtruck/PatosX/actions/workflows/ci.yml/badge.svg)](https://github.com/theofficialtruck/PatosX/actions/workflows/ci.yml)

## Features

- Moderation tools for keeping servers organized and safe.
- Economy + game-style commands (shop, drops, fishing/mining-style activities, etc.).
- AI-powered chat/commands via Google Gemini (and OpenRouter).
- Fun and utility commands for everyday server use.

## Installation

### Prerequisites

- Python 3.12+
- A Discord application + bot token
- MongoDB (local or Atlas)

### Setup

1. Clone the repository.
2. Create a virtual environment.
3. Install dependencies from `requirements.txt`.
4. Copy `.env.example` to `.env` and fill in your secrets.
5. Run the bot.

Example of setup steps (macOS/Linux/WSL):

```bash
git clone https://github.com/theofficialtruck/PatosX
cd PatosX
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Example of setup steps (Windows PowerShell):

```powershell
git clone https://github.com/theofficialtruck/PatosX
cd PatosX
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

## Configuration

PatosX uses environment variables stored in a local `.env` file. They are loaded and validated by
`core/config.py` the moment it is imported, so `python main.py` fails fast if a required value is
missing. View `.env.example` for a full list of available options.

Required:

```env
DISCORD_TOKEN=
MONGO_URI=
TENOR_API_KEY=
OPENROUTER_API_KEY=
GEMINI_API_KEYS=key1,key2
```

Common optional values:

```env
AUTHORIZED_USER_IDS=123456789012345678,234567890123456789
BOT_ADMIN_NAME=YourNameHere
BEG_DONORS=user1,user2
QUOTE_API_KEY=
```

NEVER commit your real `.env` file.

## Project structure

```text
main.py                     Bootstrap only: builds the Bot, wires global hooks, loads cogs/, runs
core/                       Shared services. No Discord commands live here.
  config.py                 .env loading/validation + every game/economy constant
  state.py                  Mongo client, all *_col collections, aiohttp session, executor, runtime dicts
  permsHelperFuncs.py       staff/permission checks, blacklist & maintenance barriers, global bot checks
  economyHelperFuncs.py     wallet/bank/inventory helpers, amount & duration parsing, monthly rewards
  xpHelperFuncs.py          xp_earn decorator, on_command_completion body, badge definitions/awarding
  errors.py                 error unwrapping, global error-handler bodies, send_hybrid_error/is_prefix
cogs/                       One file per feature domain (see the table below)
  _base_cog.py              Copy-paste template for a new cog; skipped by the loader
data/
  duckquiz_questions.py     Quiz question bank
  duckfacts.txt             Duck facts data file
tests/                      Pytest test suite (conftest.py holds the bot + per-cog fixtures)
.github/workflows/          GitHub Actions workflows (CI)
.env.example                Example environment template
requirements.txt            Python dependencies
ruff.toml                   Ruff configuration
pytest.ini                  Pytest configuration
start.sh                    Pulls latest from GitHub and restarts the systemd service
healthcheck.sh              Watchdog: detects crashes/hangs and restarts the service
claude_diagnose.sh          Investigates watchdog incidents and proposes fixes
LICENSE.md                  GNU AGPL-3.0 license text
CLA.md                      Contributor License Agreement
```

## Architecture

The bot is split into a thin bootstrap (`main.py`), a set of shared service modules (`core/`)
and one discord.py Cog per feature domain (`cogs/`). The guiding rules are: flat over clever
(no sub-packages, no plugin registry), imports flow in one direction only, and a cog is a
feature domain rather than a file-size bucket.

### `main.py`

`main.py` does nothing feature-specific. Top to bottom it:

1. Stubs `audioop` into `sys.modules` before importing discord (removed in Python 3.13).
2. Imports `core` (`core/config.py` loads `.env` and validates the required variables on import).
3. Defines `get_prefix` (per-guild prefix from `state.settings_col`, `?` by default) and builds the
   `Bot` with all intents, `help_command=None` (the Info cog provides `help`) and the usual
   `AllowedMentions`.
4. Registers the global hooks that are defined as plain functions in `core/`:
   `bot.add_check(...)` for `global_lock_check`, `ensure_guild_context` and `check_disabled`, and
   `bot.add_listener(xp.on_command_completion)`. They are never decorated inside `core/`, so those
   modules import and test without a real `Bot`.
5. Owns the **only** `on_message` that calls `bot.process_commands`. Cogs add their own
   `on_message` listeners purely for side effects; discord.py delivers the message to all of them.
6. Handles bot-level `on_ready` work (presence, opening the shared aiohttp session via
   `state.http_session()`, slash command sync),
   delegates `on_command_error` / `on_app_command_error` to `core.errors`, and closes the session
   on `on_close`.
7. `load_all_cogs()` loads every `cogs/*.py` as an extension (files starting with `_` are
   skipped); one cog failing to load is logged and does not stop the others.
8. `main()` runs `async with bot:` so `bot.close()` always runs. Closing the bot unloads every
   extension, which calls each cog's `cog_unload()` and cancels the loops that cog owns - there is
   no hardcoded list of task loops to keep in sync any more. The `finally` block then closes the
   aiohttp session.

### `core/`

| Module | Owns |
|---|---|
| `config.py` | `.env` loading with the pytest bypass (missing required vars raise `ValueError` normally but are filled with `""` under pytest), `TOKEN`/`MONGO_URI`/API keys, `AUTHORIZED_USER_IDS`, `BOT_ADMIN_NAME`, `BEG_DONORS`, `DEBUG_COMMANDS`, catch tables, riddles, tool durabilities, quiz settings, the help-section command sets (`STAFF_HELP_COMMANDS` etc.), monthly goal definitions, boost message types. |
| `state.py` | The single source of truth for mutable shared state: Mongo client/`db`, all 37 `*_col` collections (with the `_Dummy*` stubs used when no `MONGO_URI` is available), the aiohttp `session` (starts `None`), the `ThreadPoolExecutor`, `bot_locks`, `recent_errors`, and the invite cache + fetch queue. |
| `permsHelperFuncs.py` | `staff_only`, `staffperm`/`check_staff_perm`, `is_staff_user`, `is_blacklisted`, `blacklist_barrier`, maintenance mode checks, the three global checks (`check_disabled` resolves command categories via `command_category`), `check_channel`, `check_target_permission`, `has_staff_role`, `log_action` (posts to the `log_channel` saved by `.configure`; accepts `ctx=None` plus `guild=` for automated actions such as mute expiry). |
| `economyHelperFuncs.py` | `get_user`, inventory/tool/consumable normalisation, `get_balance`/`add_balance`/`subtract_balance`, `parse_amount`/`parse_time`/`add_suffix`, investment math, and the monthly-rewards helpers (`get_monthly_rewards_doc`, `increment_monthly_goal`, `check_and_award_monthly_rewards`). |
| `xpHelperFuncs.py` | The `xp_earn` decorator (works on plain functions and on Cog methods), `on_command_completion`, `BADGES`, badge role management and `check_and_award_badges`. |
| `errors.py` | `unwrap_command_error`, `is_discord_service_unavailable_error`, command-syntax / did-you-mean helpers, `handle_command_error` / `handle_app_command_error` (the old global handler bodies, taking `bot` as a parameter), `send_hybrid_error`, `is_prefix`. |

Import order inside `core/` is a strict DAG and is enforced by `tests/test_cog_loading.py`:

```text
config -> state -> permsHelperFuncs -> economyHelperFuncs -> xpHelperFuncs -> errors
```

`core/` never imports from `cogs/`. Monthly rewards live in `economyHelperFuncs` (not the XP
module) because `add_balance()` feeds the `coins_collected` goal and the graph only flows
`xp -> economy`.

### `cogs/`

Every cog is a `commands.Cog` subclass with `self.bot`, a module-level `async def setup(bot)`,
and (where it owns background loops) an `on_ready` listener that starts them once and a
`cog_unload()` that cancels them.

| Cog file (class) | Commands / responsibility |
|---|---|
| `guildcfg.py` (`GuildConfig`) | `configure`/`config`, `editconfig`, `viewconfig`, `resetconfig`, `setprefix`, `disable`/`enable`/`listdisabled` (a command name, an alias, or one of the categories in `core/config.py`'s `COMMAND_CATEGORIES`: `economy`, `moderation`, `duckgpt`, `general`; `enable`/`disable`/`listdisabled`/`override`/`help` can never be disabled), `maintenance`, `testwelcome`, `testboost`; `on_guild_join` (default prefix + badge roles), the welcome message and boost thank-you (`on_member_join`, boost system messages in `on_message`), badge roles on `on_ready`. |
| `staff_management.py` (`StaffManagement`) | `staff`, `unstaff`, `viewperms` (+ views/modal), `blacklist`, `whitelist`. |
| `vanity_invites.py` (`VanityInvites`) | `vanityroles`, `promoters`, `resetpromoters`, `invitechannel`, `invites`, `removeinvites`, `inviteleaderboard`, `resetinvites`; the rate-limited invite fetch queue, invite cache loops, invite attribution on join/leave, and the vanity status polling loop (`on_presence_update`). |
| `moderation.py` (`Moderation`) | `kick`, `ban`, `unban`, `mute`, `unmute`, `warn`, `clearwarns`, `purge`, `slowmode`, `say`, `userinfo`, `performance`, `modview` (+ views/modals), `reactionrole` (+ raw reaction listeners); Muted role setup and mute re-application on `on_ready`/`on_member_join`, the mute-expiry and Muted-role permission loops. |
| `tickets.py` (`Tickets`) | The full ticket system: `ticketsetup`, `ticketpanel`, `ticketaddbutton`, `ticketremovebutton`, `ticketeditbutton`, `ticketdeletepanel`, `ticketlist`, `ticketclose`, `ticketforceclose`, `transcript`, `transcriptsearch`, `transcriptlist`, `ticketadduser`, `ticketremoveuser`, `ticketsync`, all panel views/modals, persistent panel views on `on_ready`, and the plain-text `confirm`/`cancel` close flow in `on_message`. |
| `giveaways_polls.py` (`GiveawaysPolls`) | `giveaway` (slash-only form: prize, winners, duration, optional required roles and `role\|bonus` bonus-entry roles), `reroll`, `draw`, `poll` (form for slash, step-by-step wizard for prefix), `roles`/`roleadd`/`roleremove`, `addmoney`/`removemoney`, `drop`; giveaway resumption after restarts, poll restore/close loop, drop expiry loop, reminder loop, persistent role-claim and drop-claim views. Running giveaways are tracked in `ACTIVE_GIVEAWAYS` so `draw` finishes one through its live view (cancelling the timer, removing the buttons, announcing once); winners are drawn ticket-weighted without replacement by `pick_winners`. |
| `economy.py` (`Economy`) | `balance`, `daily`, `beg`, `deposit`, `withdraw`, `give`, `sell`, `invest`, `investstatus`, `passive`, `leaderboard`, `badges`, `monthlyrewards`, `reseteconomy`. |
| `shop.py` (`Shop`) | `shop` (+ buy/refund dropdowns), `additem`, `edititem`, `delitem`, `buy`, `use`, `inventory`; seeds the default shop items on `on_ready`. |
| `jobs_gathering.py` (`JobsGathering`) | `choosejob`, `work`, `jobstatus`, `fish`, `swim`, `hunt`, `mine`, `dig`, `bugcatch`, `crime`, `attack`/`rob`/`steal`. |
| `games_gambling.py` (`GamesGambling`) | `coinflip`, `duckroll`, `lottery`, `doorgame`, `mines`, `ducktowers`, `riddle`, `duckquiz` (+ all game views). |
| `fun.py` (`Fun`) | `slap`, `duckfact`, `duck`, `quote`, `afk`, `quackcount`, `quacktop`; AFK detection/removal and quack counting in `on_message`. |
| `duckgpt.py` (`DuckGPT`) | Gemini client rotation and generation, the @mention trigger in `on_message`, per-user conversation history, the daily history cleanup loop. |
| `stickynotes.py` (`StickyNotes`) | `stickynote`, `unstickynote`, repost-on-new-message, initial repost on `on_ready`, the repost-if-deleted loop. |
| `info.py` (`Info`) | `serverinfo`, `tutorial`, `help`. |
| `admin.py` (`Admin`) | `stop`, `override`, `onetime`, `restore`, `disableonetime`, `debug`; one-time channel enforcement in `on_message`; the watchdog `write_heartbeat` loop. |

### Shared-state rules

- All mutable shared state lives in `core/state.py`. No cog creates its own Mongo connection or
  `aiohttp.ClientSession`: HTTP calls go through `state.http_session()`, which returns the shared
  session (creating it lazily before `on_ready` or under tests) and must never be closed by callers.
- Shared state is always accessed **through the module**, never via a bare imported name:

  ```python
  from core import state

  ...
  await state.xp_col.find_one(...)  # never: from core.state import xp_col
  ```

  `state.session` starts as `None` and is only assigned in `on_ready`; a bare import would capture
  `None` forever. The test-suite also swaps collections with `monkeypatch.setattr(state, "xp_col",
  fake)`, which only works when callers resolve the attribute at call time. The same convention is
  used for the helper modules (`econ.get_user(...)`, `perms.check_channel(...)`,
  `xp.check_and_award_badges(...)`, `cfg.AUTHORIZED_USER_IDS`), so tests can patch them too.
- Cogs never import each other. The one cross-cog call (vanity role changes re-posting a sticky
  note) uses `self.bot.get_cog("StickyNotes")` at call time.
- Every event handler that used to be one big function is now fanned out per feature: the old
  `on_message` became six listeners (guild config, fun, admin, DuckGPT, sticky notes, tickets),
  `on_ready` became nine (plus the bot-level one in `main.py`), `on_member_join` became three.
  Each side-effect listener early-returns on bot authors, DMs, and boost system messages exactly
  where the original code did.
- Loops are started by the cog that owns them (from its once-guarded `on_ready`, matching where the
  old code started them) and cancelled in that cog's `cog_unload()`.

### Adding a new cog

Copy `cogs/_base_cog.py`, drop the leading underscore, rename the class, keep the module-level
`setup()`, and delete what you do not need. The loader picks it up automatically on the next
start. Register a test fixture for it in `tests/conftest.py` if you want to unit-test its commands.

## Running as a systemd service with a watchdog

For an always-on deployment, `start.sh <branch>` deploys the bot as a systemd
service (`patosx.service`, `Restart=always`) rather than running `python
main.py` directly. `Restart=always` only covers the case where the process
actually exits — it does nothing if the process is alive but stuck (an
asyncio event loop deadlock, for example), because systemd still sees it as
"active". Two extra scripts close that gap:

- **The `write_heartbeat` loop in `cogs/admin.py`** stamps `heartbeat.txt` in
  the project root with the current UTC time every 15 seconds, from its own
  independent task loop. If the event loop is ever stuck badly enough to stop
  servicing Discord, this stops updating too — it's the one external signal
  that can tell "alive" apart from "actually working".
- **`healthcheck.sh`**, run every 5 minutes via cron, restarts the service if
  systemd reports it as down, if the heartbeat file goes stale (>180s), or if
  it never appears at all after a startup grace period. It only ever
  restarts the already-deployed build (`systemctl restart`) — it never pulls
  new code; deploys stay a deliberate `./start.sh` action. Every restart it
  triggers is logged to `watchdog_incidents.log` with a status/journal
  snapshot.
- **`claude_diagnose.sh`**, run every 15 minutes via cron, checks for new
  entries in `watchdog_incidents.log` and, if there are any, runs the
  [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) headlessly
  to investigate root cause. If it finds a clear, narrowly-scoped bug, it
  pushes a fix to a new `fix/incident-*` branch for manual review — it never
  merges, never touches `main` directly, and never restarts anything itself.
  If it isn't confident, it writes its reasoning to
  `claude_diagnose_findings.log` instead of guessing.

Setup, once the service itself is running:

```bash
crontab -e
```

```cron
*/5 * * * *  /path/to/patosx/healthcheck.sh >> /path/to/patosx/watchdog_cron.log 2>&1
*/15 * * * * /path/to/patosx/claude_diagnose.sh
```

`claude_diagnose.sh` needs the `claude` CLI on `PATH` and a
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, which uses your Claude
subscription rather than separate per-token billing) in a local
`.env.diagnose` file (`chmod 600`, already gitignored) next to it:

```bash
npm install -g @anthropic-ai/claude-code
claude setup-token
echo 'export CLAUDE_CODE_OAUTH_TOKEN=...' > .env.diagnose
chmod 600 .env.diagnose
```

Both scripts require the `sudo systemctl restart patosx.service` (and
`status`/`journalctl`) commands to be passwordless for the user running cron
— see `visudo` / `/etc/sudoers.d/`. Without that, `healthcheck.sh` fails fast
and logs the problem rather than hanging cron on a password prompt.

If neither script is set up, the bot still runs fine — `write_heartbeat` is a
no-op cost (one small file write every 15s) and nothing else depends on it.

## Development tips

Before committing changes or opening a pull request, run the full test suite and format/lint the
whole tree (`ruff.toml` scopes `E402` to `main.py` and the deliberate `except Exception` error
boundaries, `BLE001`, to `main.py`, `cogs/` and `core/`):

```bash
pytest
ruff check . --fix
ruff format .
bandit -r . --exclude ./tests,./.venv,./env -s B311,B608
```

`tests/test_ci_checks.py` runs bandit, ruff and codespell over `main.py`, `core/`, `cogs/`,
`data/` and `tests/` as subprocesses so the local result matches CI; `tests/test_cog_loading.py`
checks that every extension loads, that no command was lost, that only `main.py` dispatches
commands, and that the import graph is still a DAG.

### Writing tests

`tests/conftest.py` provides a fresh, never-connected `bot` per test and one fixture per cog
(`shop_cog`, `economy_cog`, `moderation_cog`, ...). A cog fixture adds its cog to that bot, so
both ways of invoking a command work:

```python
# a command's .callback is the unbound method - pass the cog as `self`
await shop_cog.buy.callback(shop_cog, ctx, item="fishing rod 10")

# calling the Command object lets discord.py inject the cog for you
await moderation_cog.warn(ctx, member, reason="Breaking rules")
```

Shared state and helpers are patched on the module that owns them, never on `main`:

```python
from core import state, economyHelperFuncs as econ

monkeypatch.setattr(state, "xp_col", fake_collection)
monkeypatch.setattr(econ, "get_user", AsyncMock(return_value={"wallet": 100}))
```

Because no cog starts a loop until `on_ready`, constructing cogs in tests never spawns background
tasks.

## License

This project is licensed under the GNU Affero General Public License v3.0 or later.
See the `LICENSE.md` file for the full license text.

## Contributions

By submitting code, documentation, or other contributions to this repository, you agree to the terms in `CLA.md`.

## Source access

If PatosX is made available for use over a network, the corresponding source code for this project is available through this repository in accordance with the AGPL.

## Contact

Project owner: `theofficialtruck`\
PatosX's Main Discord: [PatosX](https://discord.gg/DuckParadise)

For questions, permissions, or other inquiries:

- Discord: theofficialtruck (or open a ticket in the Discord server)
- Email: `theofficialtruck@gmail.com`
- GitHub: [theofficialtruck](https://github.com/theofficialtruck)

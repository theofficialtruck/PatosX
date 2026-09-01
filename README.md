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

PatosX uses environment variables stored in a local `.env` file. `main.py` will fail if required values are missing. View .env.example for a full list of available options.

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
.github/workflows/      GitHub Actions workflows (CI)
tests/                  Pytest test suite
.env.example            Example environment template
main.py                 Main bot entry point
requirements.txt        Python dependencies
duckquiz_questions.py   Quiz question bank
duckfacts.txt           Duck facts data file
ruff.toml               Ruff configuration
pytest.ini              Pytest configuration
start.sh                Pulls latest from GitHub and restarts the systemd service
healthcheck.sh          Watchdog: detects crashes/hangs and restarts the service
claude_diagnose.sh      Investigates watchdog incidents and proposes fixes
LICENSE.md              GNU AGPL-3.0 license text
CLA.md                  Contributor License Agreement
```

## Running as a systemd service with a watchdog

For an always-on deployment, `start.sh <branch>` deploys the bot as a systemd
service (`patosx.service`, `Restart=always`) rather than running `python
main.py` directly. `Restart=always` only covers the case where the process
actually exits — it does nothing if the process is alive but stuck (an
asyncio event loop deadlock, for example), because systemd still sees it as
"active". Two extra scripts close that gap:

- **`main.py`'s `write_heartbeat` loop** stamps `heartbeat.txt` with the
  current UTC time every 15 seconds, from its own independent task loop. If
  the event loop is ever stuck badly enough to stop servicing Discord, this
  stops updating too — it's the one external signal that can tell "alive"
  apart from "actually working".
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

Before committing changes or opening a pull request, run the full test suite and format/lint the entrypoint:

```bash
pytest
ruff check . --fix
ruff format .
bandit -r . --exclude ./tests,./.venv,./env -s B311,B608
```

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

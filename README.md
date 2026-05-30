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

## Quick start script

You can also use the included startup script. It fetches the specified branch (usage: ./start.sh <branch> [--force]), sets up a venv, installs deps, then runs the bot.

Example:
```bash
./start.sh main
```

To force your local branch to match the remote branch:

```bash
./start.sh main --force
```

> Note: `start.sh` requires `git` + `bash` (works on macOS/Linux, and on Windows via WSL or Git Bash).

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
.github/workflows/     GitHub Actions workflows (CI)
tests/                 Pytest test suite
.env.example           Example environment template
main.py                Main bot entry point
start.sh               Local startup/update script
requirements.txt       Python dependencies
duckquiz_questions.py  Quiz question bank
duckfacts.txt          Duck facts data file
ruff.toml              Ruff configuration
pytest.ini             Pytest configuration
LICENSE.md             GNU AGPL-3.0 license text
CLA.md                 Contributor License Agreement
```

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

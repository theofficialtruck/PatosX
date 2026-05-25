#!/bin/bash

# Check if branch argument is provided
if [ -z "$1" ]; then
    echo "[!] Error: No branch specified."
    echo "Usage: ./start.sh <branch>"
    echo "Example: ./start.sh main"
    exit 1
fi

BRANCH="$1"
REPO_URL="https://github.com/i-am-lmi0/DuckParadise.git"
PROJECT_DIR="/home/thetruck/duckparadise"

if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo "[!] Cloning repository (branch: $BRANCH)..."
    git clone -b "$BRANCH" "$REPO_URL" "$PROJECT_DIR" || exit 1
else
    echo "[!] Pulling latest changes from $BRANCH..."
    cd "$PROJECT_DIR" || exit 1
    git fetch origin || exit 1
    git checkout "$BRANCH" || exit 1
    git pull origin "$BRANCH" || exit 1
fi

cd "$PROJECT_DIR" || exit 1

if [ ! -d venv ]; then
    python3 -m venv venv
fi

source venv/bin/activate

if [ -f requirements.txt ]; then
    pip install --upgrade -r requirements.txt
fi

python main.py

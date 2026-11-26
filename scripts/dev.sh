#!/bin/sh
set -eu

digest=$(node -p 'require("./runtime.lock.json").sha256')
config="$PWD/.runtime/$digest/api/wrangler.json"
if [ ! -f "$config" ]; then
    npm run runtime:install
fi

umask 077
mkdir -p .local/first-store
cd .local/first-store
if [ ! -f netrc ]; then
    # Publish a complete credential file even if two terminals start together.
    candidate=$(mktemp ./netrc.XXXXXX)
    trap 'rm -f "$candidate"' EXIT
    password=$(node -p 'require("node:crypto").randomBytes(32).toString("hex")')
    printf 'machine 127.0.0.1 login operator password %s\n' "$password" > "$candidate"
    ln "$candidate" netrc || [ -f netrc ]
    rm -f "$candidate"
    trap - EXIT
fi

read -r machine host login BASIC_AUTH_USERNAME password_label BASIC_AUTH_PASSWORD < netrc
export BASIC_AUTH_USERNAME BASIC_AUTH_PASSWORD
export R2_ACCESS_KEY_ID=local R2_SECRET_ACCESS_KEY=local
exec wrangler dev --local --ip 127.0.0.1 --port 8787 --config "$config" --persist-to .wrangler/state

#!/usr/bin/env bash
# Checks that this machine can run Meridian. Run from WSL: `make doctor`.
# Read-only: it reports, it never changes anything.

set -u
fail=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }
hint() { printf '        -> %s\n' "$1"; }

echo "Meridian environment check"
echo

echo "Shell"
if grep -qi microsoft /proc/version 2>/dev/null; then
  ok "running inside WSL ($(. /etc/os-release && echo "$PRETTY_NAME"))"
elif [ "$(uname -s)" = "Linux" ] || [ "$(uname -s)" = "Darwin" ]; then
  ok "running on $(uname -s)"
else
  bad "not in WSL -- this shell is $(uname -s)"
  hint "open an Ubuntu tab in Windows Terminal and run this again"
fi

echo
echo "Tools"
for t in make git; do
  if command -v "$t" >/dev/null; then ok "$t $("$t" --version | head -1 | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)"
  else bad "$t not installed"; hint "sudo apt install -y $t"; fi
done
if command -v python3 >/dev/null; then
  pv=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then ok "python3 $pv"
  else bad "python3 $pv is too old (need 3.10+)"; fi
  if python3 -c 'import ensurepip' 2>/dev/null; then ok "python3 venv support"
  else bad "python3 venv support missing"; hint "sudo apt install -y python3-venv"; fi
else
  bad "python3 not installed"; hint "sudo apt install -y python3 python3-venv"
fi
if command -v psql >/dev/null; then ok "psql $(psql --version | grep -oE '[0-9]+(\.[0-9]+)?' | head -1)"
else warn "psql not installed (optional -- 'make psql' works without it)"; hint "sudo apt install -y postgresql-client"; fi

echo
echo "Docker"
if ! command -v docker >/dev/null; then
  bad "docker command not found in WSL"
  hint "Docker Desktop -> Settings -> Resources -> WSL integration -> enable your Ubuntu distro"
elif ! docker info >/dev/null 2>&1; then
  bad "docker is installed but the engine isn't reachable"
  hint "start Docker Desktop and wait for 'Engine running', then retry"
else
  ok "docker engine $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
  if docker compose version >/dev/null 2>&1; then ok "docker compose $(docker compose version --short)"
  else bad "docker compose v2 missing"; hint "update Docker Desktop"; fi
  mem=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
  gb=$(( mem / 1024 / 1024 / 1024 ))
  if [ "$gb" -lt 4 ]; then bad "Docker can use only ${gb} GB RAM -- Phase 0 needs ~2 GB free, later phases ~8 GB"
    hint "raise memory= in C:\\Users\\<you>\\.wslconfig, then 'wsl --shutdown' from PowerShell"
  else ok "Docker can use ${gb} GB RAM (set by .wslconfig; Phase 4-5 want ~8 GB)"; fi
  if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ':5433 '; then
    if docker ps --format '{{.Names}}' | grep -qx meridian-postgres; then ok "port 5433 is Meridian's Postgres (already running)"
    else warn "something else is listening on 5433"; hint "set POSTGRES_PORT in a .env file"; fi
  else ok "port 5433 is free"; fi
fi

echo
echo "Repo"
root=$(cd "$(dirname "$0")/.." && pwd)
crlf=$(grep -rlI $'\r' "$root" --exclude-dir=.git --exclude-dir=.venv 2>/dev/null | head -5)
if [ -z "$crlf" ]; then ok "no CRLF line endings"
else bad "CRLF line endings in: $(echo "$crlf" | sed "s#$root/##" | tr '\n' ' ')"
  hint "git add --renormalize . (after git init) or set your editor to LF"; fi
if command -v git >/dev/null; then
  ac=$(git config --global core.autocrlf || true)
  if [ "$ac" = "true" ]; then warn "git core.autocrlf=true in WSL"; hint "git config --global core.autocrlf input"
  else ok "git core.autocrlf=${ac:-unset}"; fi
  if [ -n "$(git config --global user.email || true)" ]; then ok "git identity: $(git config --global user.name) <$(git config --global user.email)>"
  else warn "git user.name / user.email not set"; hint "git config --global user.name 'Your Name' && git config --global user.email you@example.com"; fi
  if [ -d "$root/.git" ]; then ok "git repo initialised"
  else warn "not a git repo yet"; hint "git init -b main"; fi
fi
venv="${MERIDIAN_VENV:-$HOME/.venvs/meridian}"
if [ -x "$venv/bin/dbt" ]; then ok "dbt $("$venv/bin/dbt" --version 2>/dev/null | grep -oE 'installed: *[0-9.]+' | grep -oE '[0-9.]+' | head -1) in $venv"
else warn "dbt not installed yet (needed for Phase 1)"; hint "make venv"; fi

echo
if [ "$fail" -eq 0 ]; then echo "Ready. Next: make seed"; else echo "Fix the FAIL lines above, then run make doctor again."; fi
exit "$fail"

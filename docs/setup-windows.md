# Running Meridian on Windows 11

Docker Desktop with the WSL2 backend, driven from a WSL shell.

## 1. Cap WSL's memory

WSL2 will otherwise take up to half your RAM and hold onto it. Create
`C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=10GB        # Phase 0 needs ~2 GB; Phase 4/5 want ~8 GB
processors=6
swap=4GB

[experimental]
autoMemoryReclaim=gradual
```

Then, from PowerShell: `wsl --shutdown`, and restart Docker Desktop.

Adjust `memory` to your machine -- leave Windows at least 4–6 GB.

## 2. Docker Desktop

Settings → General → *Use the WSL 2 based engine* on.
Settings → Resources → WSL integration → enable your Ubuntu distro.

Check from WSL: `docker version` and `docker compose version`.

## 3. make

```bash
sudo apt update && sudo apt install -y make postgresql-client
```

`postgresql-client` is optional -- it gives you `psql` in WSL so you can connect
to `localhost:5433` without going through the container.

## 4. Where the repo lives

The repo is at `C:\Users\<you>\Documents\Projects\meridian`, which WSL sees as
`/mnt/c/Users/<you>/Documents/Projects/meridian`. That works fine for this
project: the database lives in a Docker named volume, not on the Windows
filesystem, so the slow-cross-filesystem problem doesn't bite.

```bash
cd "/mnt/c/Users/<you>/Documents/Projects/meridian"   # quote it if your username has a space
make seed
```

## 5. Line endings

`.gitattributes` forces LF for everything. If you cloned before it existed, or
your editor converted files to CRLF:

```bash
git add --renormalize . && git status
```

## 6. One phase at a time

`make down` before starting another phase's stack. `make reset` deletes the
database volume entirely (the next `make seed` rebuilds it).

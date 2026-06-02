# Updating Nurby

Nurby tells you when a new release is out and gives you two ways to
update. a one-command host update, and an optional in-app one-click
button.

## How you know an update is available

Settings shows a **Software updates** card. It reports the running
version and checks GitHub for the latest release once an hour. When a
newer version exists it shows an "Update available" banner with a link
to the release notes.

The same data is on `GET /api/system/version`.

## Option 1. one command on the host (recommended)

On the machine that runs Docker Compose.

```bash
./scripts/update.sh
```

This pulls the latest code, rebuilds images, and restarts the stack.
Database migrations run automatically when the API container starts, so
there is nothing else to do. Local changes block the pull on purpose.
commit or stash them first.

## Option 2. one-click in-app button (optional, opt-in)

You can let an admin update from the Settings page with a button. This
runs a small **updater** container that performs Option 1 for you.

Enable it by starting the stack with the updater overlay.

```bash
docker compose -f docker-compose.yml -f docker-compose.update.yml up -d
```

Now the Settings update card shows an **Update now** button. Clicking it
asks the updater to pull, rebuild, run migrations, and restart. The app
is briefly unavailable while it restarts.

### Security note

The updater mounts the Docker socket, which is equivalent to root on the
host. Only enable it on a machine you control and trust, and keep Nurby
off the public internet. It is **off by default**. without the overlay
above, the update button shows the manual command instead and nothing
privileged runs.

## Versioning

The running version comes from the `VERSION` file in the repo, bumped on
each release. An optional `NURBY_BUILD_SHA` build argument records the
exact commit for support. The update check compares your version against
the latest GitHub release tag, so cut releases as Git tags / GitHub
Releases for the banner to work.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `NURBY_GITHUB_REPO` | Repo to check for releases | `Eshpelin/nurby-backend` |
| `NURBY_SELF_UPDATE` | Enable the one-click button | off |
| `NURBY_UPDATE_TRIGGER` | Path the API writes to signal the updater | `/data/update.request` |

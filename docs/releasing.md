# Releasing Nurby

Releases are automated and free. Tagging a commit builds every service
image, publishes them to the GitHub Container Registry (GHCR), and
creates a GitHub Release. This uses GitHub Actions and GHCR, both free
for public repositories.

## Cut a release

1. Bump the version so the in-app update banner matches the tag.
   ```bash
   echo "0.2.0" > VERSION
   git commit -am "release 0.2.0"
   ```
2. Tag and push.
   ```bash
   git tag v0.2.0
   git push origin main --tags
   ```

That is it. The `release` workflow then.

- Builds `api`, `mcp`, `ingestion`, `perception`, and `frontend` images.
- Pushes each to `ghcr.io/<owner>/nurby-<service>` tagged with the
  version and `latest`.
- Creates a GitHub Release with auto-generated notes.

## One-time setup for a new repo or fork

- The first time images are published, GHCR marks the packages private.
  Open each package on GitHub (Profile -> Packages) and set its
  visibility to **Public** so anyone can pull. You only do this once per
  package.
- No secrets are needed. The workflow uses the built-in `GITHUB_TOKEN`.

## How users consume releases

End users do not need to build anything. With the published images they
pull and run.

```bash
# In .env, set NURBY_REGISTRY_OWNER to your GitHub owner (and NURBY_VERSION
# to a tag, or leave it at latest), then.
docker compose pull
docker compose up -d
```

The compose file still supports building from source for development, so
`docker compose up --build` continues to work for contributors.

## Versioning notes

- The running version is read from the `VERSION` file. Keep it in sync
  with the Git tag you push.
- CI bakes the commit SHA into the API image as `NURBY_BUILD_SHA`, shown
  by `GET /api/system/version` and the Settings update card.

# bekas Documentation

Documentation site for [bekas](https://github.com/ragilhadi/bekas), built with [Astro Starlight](https://starlight.astro.build/).

## Local development

```bash
npm install
npm run dev
```

The site will be available at [http://localhost:4321](http://localhost:4321). Edits to `.md` and `.mdx` files in `src/content/docs/` reload automatically.

## Project structure

```
.
├── astro.config.mjs              # Starlight config (sidebar, theme, etc.)
├── package.json
├── tsconfig.json
├── Dockerfile                    # multi-stage build → nginx
├── docker-compose.yml
├── nginx.conf
├── public/                       # Static assets (favicon, images)
└── src/
    ├── content.config.ts         # Astro content collection config
    └── content/
        └── docs/                 # All documentation pages
            ├── index.mdx                # Landing page
            ├── start-here/
            ├── guides/
            └── reference/
```

## Adding a new page

1.  Create a `.md` or `.mdx` file under `src/content/docs/<section>/`.
2.  Add frontmatter with at least `title` and `description`:

    ```mdx
    ---
    title: My new page
    description: A short summary.
    ---
    ```

3.  Add the page to the sidebar in `astro.config.mjs` (the `sidebar` array).

Use `.mdx` if you want to import and use Starlight components (Tabs, Steps, Cards, Asides, etc.). Use `.md` for plain markdown.

## Build for production

```bash
npm run build
```

Output goes to `dist/`. It's static HTML — deploy it anywhere: behind nginx (this repo's Dockerfile), GitHub Pages, Netlify, Vercel, Cloudflare Pages, S3, etc.

## Deploy with Docker Compose

A multi-stage `Dockerfile` and `docker-compose.yml` are included. The build stage produces the static site, and the runtime stage serves it with nginx.

```bash
# Build the image and start the container
docker compose up -d --build

# View at http://localhost:8080
```

Useful commands:

```bash
docker compose logs -f          # tail logs
docker compose restart docs     # restart the service
docker compose down             # stop and remove
docker compose up -d --build    # rebuild after editing docs
```

The container exposes port `80` internally; the compose file maps it to host port `8080`. Change the left side of `"8080:80"` in `docker-compose.yml` if you need a different host port.

### Putting it behind a reverse proxy

The included nginx config sets sensible cache headers (immutable for hashed `_astro/` assets, `must-revalidate` for HTML). If you're putting Caddy, Traefik, or another nginx in front of this, just proxy to `http://bekas-docs:80` from the same Docker network — no extra config needed.

### Deploying to a remote host

The `Dockerfile` works the same way on any Docker host. The simplest workflow:

```bash
# On the server
git clone https://github.com/ragilhadi/bekas && cd bekas/docs-site
docker compose up -d --build
```

For something more automated, CI builds and pushes the image on every push to `master` that touches `docs-site/**` (see `.github/workflows/docs.yml` in the repo root). Pull the image and restart:

```bash
docker compose pull && docker compose up -d
```

(replace the `build:` block in `docker-compose.yml` with `image: <registry>/<app>:latest` first)

## CI

`.github/workflows/docs.yml` (repo root) builds the site image on pushes to `master` touching `docs-site/**` or the package version (single source: `src/bekas/__init__.py`), validates it against `^v[0-9]+\.[0-9]+\.[0-9]+$`, tags the image with that version plus `latest`, and pushes to the private registry configured via repository secrets:

| Secret | Purpose |
|---|---|
| `REGISTRY_HOST` | Registry hostname |
| `APP_NAME` | Image name |
| `REGISTRY_USERNAME` / `REGISTRY_PASSWORD` | Registry credentials |

The workflow only runs on pushes to `master` (merge) — pull requests don't trigger it.

## Resources

-   [Starlight docs](https://starlight.astro.build/) — components, configuration, customization.
-   [Astro docs](https://docs.astro.build/) — the underlying framework.
-   [bekas repo](https://github.com/ragilhadi/bekas) — the project being documented.

# Host an Apkg Server

An Apkg server receives release bundles and exposes their packages through a signed APT repository. This chapter sets up one server with SQLite and persistent storage. You can add MySQL, upstream mirrors, or separate download nodes later.

You do not need a server for local builds. If that is your goal, start with [building a package](Build-Your-First-Package.md).

## Prepare the host

Use a Linux server with Docker and the Compose plugin. Check the tools before starting:

```bash
docker --version
sudo docker compose version
```

If either is missing, install Docker Engine and its Compose plugin using the [Docker installation guide](https://docs.docker.com/engine/install/ubuntu/) for your server's Ubuntu base release. Choose storage based on the packages you intend to retain. A small private repository and an upstream distribution mirror have very different disk and bandwidth requirements.

```bash title="Prepare service files"
mkdir -p ~/Source/ServiceConfigs/Apkg
cd ~/Source/ServiceConfigs/Apkg
mkdir -p data
```

Create `compose.yaml`:

```yaml title="compose.yaml"
services:
  apkg:
    image: aiursoft/apkg:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:5000:5000"
    environment:
      ConnectionStrings__DbType: Sqlite
      ConnectionStrings__DefaultConnection: "DataSource=/data/app.db;Cache=Shared"
      Storage__Path: /data
      Storage__ExportPath: /data/export
      AppSettings__Local__AllowRegister: "false"
      AppSettings__Local__AllowWeakPassword: "false"
    volumes:
      - ./data:/data
```

The port is initially reachable only on the server itself. If port 5000 is already occupied, select another host port; do not terminate an unrelated service. `latest` is convenient for a first trial. Record the image digest after pulling and pin an evaluated digest for repeatable production deployments.

```bash title="Validate and start"
sudo docker compose config --quiet
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=100 apkg
curl --fail http://127.0.0.1:5000/health
```

An initial startup creates the database and signing material, so allow time for initialization. From a remote workstation, use an SSH tunnel:

```bash title="Run on your workstation"
ssh -L 5000:127.0.0.1:5000 your-user@your-server
```

Open `http://localhost:5000` in your browser while the tunnel remains connected.

## Initialize the administration account

On a new database, the source seeds the login `admin@default.com` with password `Admin@123456!`. Sign in and change this password immediately through the account's password settings, before exposing the service publicly. Existing databases retain their existing accounts.

The Compose configuration disables public registration. Create the accounts and role permissions your maintainers need through the administration interface. API keys inherit their owner's authority; creating a key does not grant permission to publish another user's package.

!!! note "Review the seeded examples"

    A fresh instance also creates example Ubuntu mirrors and linked repositories. Background mirror jobs are scheduled automatically. For this small standalone tutorial, remove the unused example repositories and their mirrors through the web interface after initialization. Do not leave an unwanted upstream synchronization configured on a small server. Keep or create a signing certificate for your own repository.

## Create a signed standalone repository

In **Engine → Certificates**, generate a key for your repository, or review the initial generated key for a private trial. Use a short identifier such as `myrepo` for its name and record its fingerprint. Keep the private key on the server and distribute only the public key.

In **Engine → Public Repositories** (`/Repositories`), create a repository with:

| Field | Tutorial value |
|-------|----------------|
| Name | `My AnduinOS packages` |
| Distro | `anduinos` |
| Suite | `resolute-addon` |
| Components | `main` |
| Architecture | `amd64,arm64` |
| Mirror | `None (Standalone)` |
| Enable GPG Signing | Enabled |
| Certificate | Your chosen certificate |

The repository's architecture field lists the client indexes to generate. A package with `Architecture: all`, such as `hello-apkg`, can appear in both indexes. In contrast, a compiled `amd64` payload needs a matching architecture. Keep the component and suite aligned with your `.aosproj`; the display name alone has no effect on routing.

A standalone repository hosts your additions while clients continue to use their normal Ubuntu/AnduinOS sources for dependencies. It does not require a full Ubuntu mirror. If you later link a mirror, review its suite, components, signature verification, bandwidth, and the precedence of locally published packages before exposing that repository to users.

Record the repository ID from its details page. The source configuration endpoint is `/api/sources/ID`; the ID is assigned by your instance and is not necessarily `1`.

## Publish through an HTTPS domain

For clients outside the SSH tunnel, configure a domain you own, for example `apkg.example.com`, pointing to the server. Open ports 80 and 443 for your reverse proxy. The following optional Compose override runs Caddy on the same Docker network.

Create `Caddyfile`, replacing the example domain:

```text title="Caddyfile"
apkg.example.com {
    reverse_proxy apkg:5000
}
```

Create `compose.https.yaml`:

```yaml title="compose.https.yaml"
services:
  apkg:
    environment:
      GlobalSettings__PublicAptServerDomain: "https://apkg.example.com"
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy-data:/data
      - ./caddy-config:/config
    depends_on:
      - apkg
```

```bash title="Enable HTTPS after account initialization"
sudo docker compose -f compose.yaml -f compose.https.yaml config --quiet
sudo docker compose -f compose.yaml -f compose.https.yaml up -d
curl --fail https://apkg.example.com/health
```

Use both `-f` arguments in subsequent Compose operations for this deployment. If you already have a reverse proxy, add an equivalent route there instead of binding a second service to its ports. Caddy requires valid DNS and reachable challenge ports to obtain a certificate.

`PublicAptServerDomain` controls the base URL advertised for package downloads and public keys. Use the origin, without an `/artifacts` suffix. A setting supplied through the environment overrides the corresponding web setting. For AppStream screenshots, configure this value explicitly because background jobs cannot infer the public origin from a browser request.

## Know what must persist

The `data/` directory contains the SQLite database, uploaded package data, and persisted application configuration. Signing keys and repository state must survive container replacement. In this example, static exports also live there, under `data/export/`.

For MySQL deployments, configure `ConnectionStrings__DbType=MySql` and a MySQL connection string, and back up the database service separately from package storage. The [server configuration reference](Server-Configuration.md) describes these settings. Do not simply change database providers on an initialized deployment and expect its data to migrate.

The source also includes a [systemd installation script](https://github.com/AiursoftWeb/Apkg/blob/master/install.sh). It installs build prerequisites, publishes the web application into `/opt/apps/apkg`, and registers a service. That is an alternative to this container deployment, not the command for installing the developer CLI.

Next: [upload the first package and verify APT installation](Publish-Packages.md). Server health alone does not yet prove that your repository contains a signed, installable package.

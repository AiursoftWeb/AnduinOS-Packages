# Common Docker Tips and Tricks

The following are some useful Docker commands and techniques that can help you manage your containers and images more effectively. Each section includes explanations of when and how to use these commands.

## Install Docker

To install Docker on AnduinOS, please follow the instructions [here](../../Applications/Development/Docker/Docker.md).

!!! tip "Also install recommended plugins"

    It is recommended to also install the following packages at the same time:

    | Package | Purpose |
    |---|---|
    | `qemu-user-binfmt` | Enables cross-architecture emulation via the kernel's `binfmt_misc` |
    | `docker-buildx` | BuildKit-backed builder — required for multi-architecture image builds |
    | `docker-compose-v2` | Compose v2 plugin (`docker compose`) for multi-container application management |

    ```bash
    sudo apt install -y qemu-user-binfmt docker-buildx docker-compose-v2
    ```

## Build an Image from a Dockerfile

!!! warning "Legacy build command"

    `docker build` uses the classic builder and **does not support multi-architecture targets**. It is gradually being superseded by `docker buildx build`, which is backed by BuildKit and is the recommended approach going forward. See the [Build Multi-Architecture Images with `docker buildx`](#build-multi-architecture-images-with-docker-buildx) section at the bottom of this page for details.

```bash title="Build an Image from a Dockerfile"
docker build -t image_name:tag .
```

## Run a Command Inside a Running Container

```bash title="Run a Command Inside a Running Container"
docker exec -it container_id_or_name bash
```

## Map Ports Between Host and Container

```bash title="Map Ports Between Host and Container"
docker run -p host_port:container_port image_name
```

## Use Volumes to Persist Data

```bash title="Use Volumes to Persist Data"
docker run -v /host/path:/container/path image_name
```

## Set Environment Variables in a Container

```bash title="Set Environment Variables in a Container"
docker run -e VARIABLE_NAME=value image_name
```

## Limit Container Resources

### Limit CPU Usage:

```bash title="Limit CPU Usage"
docker run --cpus="1.5" image_name
```

### Limit Memory Usage:

```bash title="Limit Memory Usage"
docker run --memory="500m" image_name
```

## Check Container Logs

```bash title="Check Container Logs"
docker logs container_id_or_name
```

## Remove Dangling Images

```bash title="Remove Dangling Images"
docker image prune -f
```

## Inspect a Container or Image

### Inspect a Container:

```bash title="Inspect a Container"
docker inspect container_id_or_name
```

### Inspect an Image:

```bash title="Inspect an Image"
docker inspect image_name:tag
```

## Use `docker compose` to Manage Multi-Container Applications

### `docker-compose.yml` Example:

```yaml
version: '3'
services:
  web:
    image: nginx:latest
    ports:
      - "80:80"
  db:
    image: postgres:latest
    environment:
      - POSTGRES_PASSWORD=example
```

### Run the Application:

```bash title="Run the Application"
docker compose up -d
```

## Tag and Push an Image to a Registry

### Tag the Image:

```bash title="Tag the Image"
docker tag local_image:tag username/repository:tag
```

### Push the Image:

```bash title="Push the Image"
docker push username/repository:tag
```

## Run a Container in Detached Mode

```bash title="Run a Container in Detached Mode"
docker run -d image_name
```

## Remove All Stopped Containers

```bash title="Remove All Stopped Containers"
docker container prune -f
```

## Save and Load Docker Images

### Save an Image to a File:

```bash title="Save an Image to a File"
docker save -o image.tar image_name:tag
```

### Load an Image from a File:

```bash title="Load an Image from a File"
docker load -i image.tar
```

## Pull the Latest Version of an Image

```bash title="Pull the Latest Version of an Image"
docker pull image_name:latest
```

## Stop and Remove All Containers

```bash title="Stop and Remove All Containers"
docker stop $(docker ps -aq)
docker rm $(docker ps -aq)
```

## Run a Container with a Specific Restart Policy

```bash title="Run a Container with Restart Policy"
docker run --restart unless-stopped image_name
```

## Connect a Container to a Network

```bash title="Connect a Container to a Network"
docker network create my_network
docker run --network my_network image_name
```

## Run as host user

```bash title="Run as host user"
docker run --user $(id -u):$(id -g) -it container_name bash
```

## Initialize Docker Swarm as Admin

To initialize a Docker Swarm and advertise the manager node's IP address:

```bash title="Initialize Docker Swarm"
sudo docker swarm init --advertise-addr $(hostname -I | awk '{print $1}')
```

## Copy Files Between Host and Container

### To Container:

```bash title="Copy a file from the host to a container"
docker cp foo.txt container_id:/foo.txt
```

### From Container:

```bash title="Copy a file from a container to the host"
docker cp container_id:/foo.txt foo.txt
```

## MySQL Docker Backup and Restore

### Backup a MySQL Database:

```bash title="Backup a MySQL Database"
sudo docker exec <container_id_or_name> sh -c 'exec mysqldump -u root -p"<root_password>" <database_name>' > ./backup.sql
```

### Backup a MariaDB Database:

```bash title="Backup a MariaDB Database"
sudo docker exec <container_id_or_name> sh -c 'exec mysqldump -u root -p"<root_password>" <database_name>' > ./backup.sql
```

### Restore a MySQL Database:

```bash title="Restore a MySQL Database"
sudo docker exec -i <container_id_or_name> sh -c 'exec mysql   -u root -p"<root_password>" <database_name>' < ./backup.sql
```

### Restore a MariaDB Database:

```bash title="Restore a MariaDB Database"
sudo docker exec -i <container_id_or_name> sh -c 'exec mariadb -u root -p"<root_password>" <database_name>' < ./backup.sql
```

## Sort Containers by Resource Usage

### RAM Usage

```bash title="Sort Containers by RAM Usage"
sudo docker stats --no-stream --format "table {{.Name}}\t{{.Container}}\t{{.MemUsage}}" | sort -k 3 -h 
```

### CPU Usage

```bash title="Sort Containers by CPU Usage"
sudo docker stats --no-stream --format "table {{.Name}}\t{{.Container}}\t{{.CPUPerc}}" | sort -k 3 -h
```

### Unhealthy

```bash title="Sort Unhealthy Containers"
sudo docker ps  --filter "health=unhealthy" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}" | sort -k 3 -h
```

Or simply list all containers with their health status:

```bash title="List all containers with their health status"
sudo docker ps --format "table {{.Names}}\t{{.Status}}\t{{.State}}"
```

Or if you are using Docker Swarm, you can list services with unhealthy replicas:

```bash title="List docker swarm services with unhealthy replicas"
sudo docker service ls --format '{{.Name}} {{.Replicas}}' | awk '{ split($2, a, "/"); if (a[1] != a[2]) print $0 }'
```

### Start Time

```bash title="List all containers sort by start time"
sudo docker ps -aq | xargs -n1 sudo docker inspect --format '{{.Id}} {{.Name}} {{.State.StartedAt}}'   | sort -k3  | awk '{print $3, $2, $1}'
```

### Disk Usage

```bash title="Sort Containers by Disk Usage"
sudo docker ps --format "table {{.ID}}\t{{.Names}}\t{{.Size}}" | sort -k 3 -h
```

### Image Size

```bash title="Sort Images by Size"
sudo docker images --format "{{.ID}}\t{{.Size}}\t{{.Repository}}" | sort -k 2 -h
```

## Get Disk Space Usage

```bash title="Shows the disk space used by Docker images, containers, and volumes."
sudo docker system df
```

## Remove Useless Images and Delete Killed Containers and Volumes

```bash
sudo docker system prune -a --volumes -f
```

## Browse Image Content

```bash
sudo docker run -it --entrypoint sh image_name
```

## Output Secret Value

```bash
get_docker_secret() {
  if [ -z "$1" ]; then
    echo "Usage: get_docker_secret <secret_id>"
    return 1
  fi
  secret_id=$1
  service_name="secret-reader-$secret_id"
  sudo docker service create --name "$service_name" --secret "$secret_id" alpine sh -c "cat /run/secrets/$secret_id && sleep 10"
  # Wait for the service task to reach "running" state before fetching logs
  for _ in $(seq 1 10); do
    state=$(sudo docker service ps "$service_name" --format '{{.CurrentState}}' --no-trunc 2>/dev/null | head -1)
    case "$state" in
      *Running*) break ;;
    esac
    sleep 1
  done
  sudo docker service logs "$service_name"
  sudo docker service rm "$service_name"
}
```

**Explanation:**

This function retrieves the value of a Docker secret by:

- Checking if a secret ID is provided.
- Creating a temporary Docker service that mounts the secret.
- Outputting the secret's content to the logs.
- Removing the temporary service after retrieval.

**When to use:**

Use this function when you need to read the value of a Docker secret, especially in situations where you need to verify the secret's content.

---

## Install `tzdata` in Dockerfile

```Dockerfile
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install -y tzdata && \
    echo "Etc/UTC" > /etc/timezone && \
    ln -fs /usr/share/zoneinfo/UTC /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata
```

**Explanation:**

- Installs the `tzdata` package without interactive prompts.
- Sets the timezone to UTC.
- Reconfigures `tzdata` to apply the timezone settings.

**When to use:**

Include this in your Dockerfile when your application depends on correct timezone settings or requires `tzdata` to function properly.

---

## Install GUI Applications

You can install GUI applications in Docker containers. For example, here's how to install WeChat:

```Dockerfile
FROM ubuntu:22.04

# Install locales
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install -y libc-bin locales wget sudo && \
    locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

# Install tzdata
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    apt-get install -y tzdata && \
    echo "Etc/UTC" > /etc/timezone && \
    ln -fs /usr/share/zoneinfo/UTC /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata

# Necessary packages
RUN apt install -y dbus-x11 packagekit-gtk3-module
RUN dbus-uuidgen > /var/lib/dbus/machine-id

# Install the app
RUN wget -O- https://deepin-wine.i-m.dev/setup.sh | sh
RUN apt install -y com.qq.weixin.deepin

ENTRYPOINT ["/opt/apps/com.qq.weixin.deepin/files/run.sh"]

# To build, run:
# sudo docker build -t wechat .

# To run, run:
# xhost +local:docker
# sudo docker run -it --rm -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix --device /dev/dri wechat
```

**Explanation:**

- **Base Image**: Uses the official `ubuntu:26.04` image from Docker Hub.
- **Locales**: Sets up locale configurations.
- **Timezone**: Installs and configures `tzdata`.
- **Dependencies**: Installs packages required for GUI applications.
- **DBus**: Generates a machine ID for D-Bus.
- **Install WeChat**: Downloads and installs WeChat using the Deepin Wine installer.
- **ENTRYPOINT**: Specifies the command to run when the container starts.

**How to Build and Run:**

- **Build the Image**:

  ```bash
  sudo docker build -t wechat .
  ```

- **Run the Container**:

  ```bash
  xhost +local:docker
  sudo docker run -it --rm -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix --device /dev/dri wechat
  ```

**When to use:**

Use this Dockerfile when you need to run GUI applications inside a Docker container, such as for testing or development purposes. The setup allows the container to display GUI applications on the host's X server.

---

## Build Multi-Architecture Images with `docker buildx`

!!! info "Why `buildx`?"

    Supporting both **x86 (AMD64)** and **ARM64** in a single image tag is now a baseline expectation for public container images. The classic `docker build` command cannot produce a multi-architecture manifest. `docker buildx`, powered by **BuildKit**, solves this cleanly — one command, one tag, every architecture.

### 1. Install Prerequisites

To cross-compile for ARM on a regular x86 machine you need **QEMU user-mode emulation** and the **buildx plugin**:

```bash
sudo apt install -y qemu-user-binfmt docker-buildx
```

`qemu-user-binfmt` registers itself with the Linux kernel's `binfmt_misc` subsystem. From that point on, the kernel silently hands any foreign binary (e.g., an ARM64 executable) to the right QEMU translator instead of refusing to run it — which is how an x86 host can execute ARM container layers.

### 2. Create and Bootstrap a Builder

The default Docker builder does not support multi-architecture output. Create a dedicated **BuildKit**-backed builder and set it as the active one:

```bash
# Create a new builder instance and switch to it immediately
docker buildx create --use --name mybuilder

# Pull the BuildKit engine container and verify the builder is healthy
docker buildx inspect --bootstrap
```

!!! note "A background container appears"

    After `--bootstrap`, running `docker ps` will reveal a container named `buildx_buildkit_mybuilder0` using the `moby/buildkit` image. **This is expected.** BuildKit offloads all compilation work to this dedicated container, which gives it capabilities the standard Docker Daemon lacks (multi-arch manifest lists, advanced caching, parallelism). It is idle when not building and consumes negligible resources.

    To pause it: `docker buildx stop mybuilder`  
    To destroy it (and its cache): `docker buildx rm mybuilder`

### 3. Verify Supported Platforms

```bash
docker buildx ls
```

The output should list a wide range of platforms such as `linux/amd64`, `linux/arm64`, `linux/riscv64`, etc., thanks to the QEMU registration performed in step 1.

### 4. Manage Builders

| Task | Command |
|---|---|
| List all builders | `docker buildx ls` |
| Inspect a builder | `docker buildx inspect mybuilder` |
| Prune build cache | `docker buildx prune -a` |
| Stop a builder | `docker buildx stop mybuilder` |
| Remove a builder | `docker buildx rm mybuilder` |

### 5. Build and Push a Multi-Architecture Image

Replace `docker build` with `docker buildx build` and add `--platform`:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t hub.yourdomain.com/my-app:latest \
  --push \
  .
```

!!! warning "Always use `--push` for multi-arch builds"

    A multi-architecture image is not a single fat blob — it is a **Manifest List** (or OCI Index) that acts as a directory: `amd64 → digest A`, `arm64 → digest B`. The traditional local Docker daemon cannot store a Manifest List. Without `--push`, BuildKit has nowhere to assemble it.

    If you do not have a registry available yet, export to an OCI tar instead:

    ```bash
    docker buildx build \
      --platform linux/amd64,linux/arm64 \
      -o type=oci,dest=/tmp/my-app-multi.tar \
      .
    ```

### 6. How Cross-Compilation Actually Works

When `buildx` builds the ARM64 variant on your x86 machine:

1. The kernel intercepts every ARM binary encountered during the `RUN` steps.
2. It transparently forwards them to `qemu-aarch64-static` (registered by `qemu-user-binfmt`).
3. QEMU translates ARM instructions to x86 on the fly and returns the results.

The container never knows it is not running on real ARM hardware. The trade-off is speed — cross-compiled `RUN` steps are slower than native. Actual compile artefacts (the final binaries) are correct native ARM64 code.

### 7. How the Manifest List Works on Pull

When a client runs `docker pull my-app:latest`:

1. Docker fetches the **Manifest List** (the "menu").
2. It reads the host's CPU architecture.
3. It downloads **only** the matching image layers — no wasted bandwidth.

No image duplication: one tag, zero redundancy.

### 8. Test an ARM Image on an x86 Machine

Thanks to QEMU you can run the ARM variant locally without any extra setup:

```bash
docker run --rm -it --platform linux/arm64 hub.yourdomain.com/my-app:latest uname -m
# Expected output: aarch64
```

### 9. CI/CD: Inject QEMU Without Installing Anything on the Host

In most CI environments (GitLab Runner, GitHub Actions) you should avoid running `apt install` on the host. The official way to register QEMU inside a CI job that only has Docker access is:

```bash
docker run --privileged --rm tonistiigi/binfmt --install all
```

This image is maintained by **Tõnis Tiigi**, the primary author of BuildKit. It mounts the host kernel's `binfmt_misc` filesystem (requires `--privileged`), registers all QEMU translators, then immediately exits (`--rm`). Nothing is left running; the kernel retains the registrations.

A complete, portable CI initialisation block:

```yaml title=".gitlab-ci.yml (before_script)"
before_script:
  # Register multi-architecture QEMU support without touching the host OS
  - docker run --privileged --rm tonistiigi/binfmt --install all
  # Create an isolated builder for this job
  - docker buildx create --use --name builder-${CI_JOB_ID}
  - docker buildx inspect --bootstrap
```

### 10. Common Pitfalls

!!! danger "Architecture-specific dependencies break cross-compilation"

    If your Dockerfile downloads a pre-built binary or installs a package that only exists for one architecture (e.g., `gcc-x86-64-linux-gnu`), the ARM64 build leg will fail. **If any architecture fails, the entire `buildx` task fails and nothing is pushed.**

    The fix is to make your Dockerfile architecture-aware. BuildKit exposes `TARGETARCH` as a build argument automatically:

    ```dockerfile
    ARG TARGETARCH
    RUN curl -Lo /usr/local/bin/mytool \
        "https://example.com/releases/mytool-linux-${TARGETARCH}" && \
        chmod +x /usr/local/bin/mytool
    ```

!!! tip "Verify a published multi-arch image with `regctl`"

    [`regctl`](https://github.com/regclient/regclient) is an open-source registry CLI that lets you inspect remote manifests:

    ```bash
    regctl image manifest hub.yourdomain.com/my-app:latest
    ```

    You should see a JSON structure containing a `manifests` array with separate entries for `linux/amd64` and `linux/arm64`, each with its own digest. If you see that, your multi-architecture image is correctly published.

### 11. Advanced: Speed Up Builds with Native Cross-Compilation

Running heavy compilation steps (e.g., `npm install`, `dotnet build`, `go build`) inside QEMU is notoriously slow — QEMU translates every single instruction at runtime. For compile-heavy images you can side-step this entirely by running the build tools on the **host's native CPU** and only targeting the foreign architecture as the output.

BuildKit provides two automatic build arguments to make this possible:

| Argument | Value | Example |
|---|---|---|
| `$BUILDPLATFORM` | Platform of the machine running the build | `linux/amd64` |
| `$TARGETPLATFORM` / `$TARGETARCH` | Platform/arch the image is built *for* | `linux/arm64` / `arm64` |

The pattern is to use `FROM --platform=$BUILDPLATFORM` for any stage that only runs build tooling, and switch to the default (target) platform for the final stage:

```dockerfile
# Stage 1: build on the HOST architecture (native speed, no QEMU)
FROM --platform=$BUILDPLATFORM golang:1.22 AS builder
ARG TARGETARCH
WORKDIR /app
COPY . .
# The Go toolchain cross-compiles to TARGETARCH without QEMU
RUN GOARCH=$TARGETARCH go build -o myapp main.go

# Stage 2: final image uses the TARGET architecture
FROM alpine:latest
COPY --from=builder /app/myapp /usr/local/bin/
ENTRYPOINT ["myapp"]
```

!!! tip "Why this works"

    Go, Rust (`--target`), and most modern compilers support native cross-compilation — they can emit ARM64 machine code while running on an x86 CPU without QEMU. Only the *output binary* is ARM64. The compilation process itself runs at full native speed.

    For interpreted runtimes (Python, Node.js) that don't have a cross-compile mode, QEMU is still needed for `RUN` steps, but you can still use native stages to handle any ahead-of-time asset building before handing off to the QEMU-based stage.

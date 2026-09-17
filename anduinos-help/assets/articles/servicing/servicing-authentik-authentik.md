# Host Authentik

Authentik is an open-source identity provider focused on flexibility and versatility. It is a self-hosted alternative to services like Auth0, Okta, and Azure AD. Authentik supports multiple authentication protocols including OAuth2, SAML, LDAP, and SCIM, and provides single sign-on capabilities for your self-hosted services.

To host Authentik on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/Authentik
cd ~/Source/ServiceConfigs/Authentik
```

Make sure no other process is taking 9000 and 9443 ports on your machine.

```bash title="Check if the ports are occupied"
function port_exist_check() {
  if [[ 0 -eq $(sudo lsof -i:"$1" | grep -i -c "listen") ]]; then
    echo "$1 is not in use"
    sleep 1
  else
    echo "Warning: $1 is occupied"
    sudo lsof -i:"$1"
    echo "Will kill the occupied process in 5s"
    sleep 5
    sudo lsof -i:"$1" | awk '{print $2}' | grep -v "PID" | sudo xargs kill -9
    echo "Killed the occupied process"
    sleep 1
  fi
}

port_exist_check 9000
port_exist_check 9443
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  postgresql:
    image: postgres:16-alpine
    volumes:
      - authentik-db:/var/lib/postgresql/data
    environment:
      POSTGRES_PASSWORD: authentik_pg_password
      POSTGRES_USER: authentik
      POSTGRES_DB: authentik
    networks:
      - internal
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -d authentik -U authentik"]
      start_period: 20s
      interval: 30s
      retries: 5
      timeout: 5s
    deploy:
      resources:
        limits:
          memory: 2G

  redis:
    image: redis:alpine
    volumes:
      - authentik-redis:/data
    networks:
      - internal
    healthcheck:
      test: ["CMD-SHELL", "redis-cli ping | grep PONG"]
      start_period: 20s
      interval: 30s
      retries: 5
      timeout: 3s
    command: --save 60 1 --loglevel warning
    deploy:
      resources:
        limits:
          memory: 1G

  server:
    depends_on:
      - postgresql
      - redis
    image: ghcr.io/goauthentik/server:2024.12
    volumes:
      - authentik-media:/media
      - authentik-certs:/certs
      - authentik-templates:/templates
    environment:
      - AUTHENTIK_SECRET_KEY=changeme_secret_key_here
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=authentik_pg_password
    ports:
      - target: 9000
        published: 9000
        protocol: tcp
        mode: host
      - target: 9443
        published: 9443
        protocol: tcp
        mode: host
    networks:
      - internal
    command: server
    deploy:
      resources:
        limits:
          memory: 4G

  worker:
    depends_on:
      - postgresql
      - redis
    image: ghcr.io/goauthentik/server:2024.12
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - authentik-media:/media
      - authentik-certs:/certs
      - authentik-templates:/templates
    environment:
      - AUTHENTIK_SECRET_KEY=changeme_secret_key_here
      - AUTHENTIK_REDIS__HOST=redis
      - AUTHENTIK_POSTGRESQL__HOST=postgresql
      - AUTHENTIK_POSTGRESQL__USER=authentik
      - AUTHENTIK_POSTGRESQL__NAME=authentik
      - AUTHENTIK_POSTGRESQL__PASSWORD=authentik_pg_password
    networks:
      - internal
    command: worker
    deploy:
      resources:
        limits:
          memory: 4G

networks:
  internal:
    driver: overlay

volumes:
  authentik-db:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/authentik/db
  authentik-redis:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/authentik/redis
  authentik-media:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/authentik/media
  authentik-certs:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/authentik/certs
  authentik-templates:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/authentik/templates
EOF
sudo mkdir -p /swarm-vol/authentik/db
sudo mkdir -p /swarm-vol/authentik/redis
sudo mkdir -p /swarm-vol/authentik/media
sudo mkdir -p /swarm-vol/authentik/certs
sudo mkdir -p /swarm-vol/authentik/templates
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml authentik --detach
```

That's it! You have successfully hosted Authentik on AnduinOS.

You can access Authentik by visiting `http://localhost:9000` in your browser.

To complete the initial setup, visit `http://localhost:9000/if/flow/initial-setup/` in your browser to create the default admin user.

## Uninstall

To uninstall Authentik, run the following commands:

```bash title="Uninstall Authentik"
sudo docker stack rm authentik
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data, certs, and database files, run the following commands:

```bash title="Remove the data, certs, and database files"
sudo rm /swarm-vol/authentik -rf
```

That's it! You have successfully uninstalled Authentik from AnduinOS.

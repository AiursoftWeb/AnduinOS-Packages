# Host PeerTube

PeerTube is a free and open-source, decentralized, federated video platform powered by ActivityPub and WebTorrent. It allows you to self-host your own video streaming service. PeerTube uses a peer-to-peer protocol to reduce the load on individual servers and supports federation with other PeerTube instances through ActivityPub.

To host PeerTube on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/PeerTube
cd ~/Source/ServiceConfigs/PeerTube
```

Make sure no other process is taking 9000 port on your machine.

```bash title="Check if the port is occupied"
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
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.8'

services:
  peertube:
    depends_on:
      - postgres
      - redis
    image: chocobozzz/peertube:production-bookworm
    volumes:
      - peertube-assets:/app/client/dist
      - peertube-data:/data
      - peertube-config:/config
    environment:
      - POSTGRES_USER=peertube
      - POSTGRES_PASSWORD=peertube_password
      - POSTGRES_DB=peertube
      - PEERTUBE_DB_USERNAME=peertube
      - PEERTUBE_DB_PASSWORD=peertube_password
      - PEERTUBE_DB_HOSTNAME=postgres
      - PEERTUBE_DB_SSL=false
      - PEERTUBE_WEBSERVER_HOSTNAME=localhost
      - PEERTUBE_WEBSERVER_PORT=9000
      - PEERTUBE_WEBSERVER_HTTPS=false
      - PEERTUBE_SECRET=changeme_secret_key_here
      - PEERTUBE_ADMIN_EMAIL=admin@example.com
      - PEERTUBE_SIGNUP_ENABLED=false
      - PEERTUBE_TRANSCODING_ENABLED=true
    ports:
      - target: 9000
        published: 9000
        protocol: tcp
        mode: host
    networks:
      - internal
    deploy:
      resources:
        limits:
          memory: 4G

  postgres:
    image: postgres:13-alpine
    environment:
      - POSTGRES_USER=peertube
      - POSTGRES_PASSWORD=peertube_password
      - POSTGRES_DB=peertube
    volumes:
      - peertube-db:/var/lib/postgresql/data
    networks:
      - internal
    deploy:
      resources:
        limits:
          memory: 2G

  redis:
    image: redis:6-alpine
    volumes:
      - peertube-redis:/data
    networks:
      - internal
    deploy:
      resources:
        limits:
          memory: 1G

networks:
  internal:
    driver: overlay

volumes:
  peertube-assets:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/peertube/assets
  peertube-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/peertube/data
  peertube-config:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/peertube/config
  peertube-db:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/peertube/db
  peertube-redis:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/peertube/redis
EOF
sudo mkdir -p /swarm-vol/peertube/assets
sudo mkdir -p /swarm-vol/peertube/data
sudo mkdir -p /swarm-vol/peertube/config
sudo mkdir -p /swarm-vol/peertube/db
sudo mkdir -p /swarm-vol/peertube/redis
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml peertube --detach
```

That's it! You have successfully hosted PeerTube on AnduinOS.

You can access PeerTube by visiting `http://localhost:9000` in your browser.

The default admin credentials are displayed in the container logs on first startup. You can view them with:

```bash title="Get admin credentials"
sudo docker service logs peertube_peertube 2>&1 | grep -A3 "User password"
```

## Uninstall

To uninstall PeerTube, run the following commands:

```bash title="Uninstall PeerTube"
sudo docker stack rm peertube
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data, config, and database files, run the following commands:

```bash title="Remove the data, config, and database files"
sudo rm /swarm-vol/peertube -rf
```

That's it! You have successfully uninstalled PeerTube from AnduinOS.

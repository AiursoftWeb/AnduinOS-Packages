# Host Vaultwarden

Vaultwarden is an unofficial Bitwarden compatible server written in Rust. It is a lightweight alternative to the official Bitwarden server that allows you to self-host your password manager. It is compatible with all Bitwarden clients, including mobile apps, browser extensions, and desktop apps.

To host Vaultwarden on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/Vaultwarden
cd ~/Source/ServiceConfigs/Vaultwarden
```

Make sure no other process is taking 80 port on your machine.

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

port_exist_check 80
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  app:
    image: vaultwarden/server:latest
    volumes:
      - vaultwarden-data:/data/
    ports:
      - target: 80
        published: 80
        protocol: tcp
        mode: host
    environment:
      - DOMAIN=http://localhost
    deploy:
      resources:
        limits:
          memory: 4G

volumes:
  vaultwarden-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/vaultwarden/data
EOF
sudo mkdir -p /swarm-vol/vaultwarden/data
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml vaultwarden --detach
```

That's it! You have successfully hosted Vaultwarden on AnduinOS.

You can access Vaultwarden by visiting `http://localhost` in your browser.

The first user to register will become the admin. You can also access the admin panel at `http://localhost/admin`.

## Uninstall

To uninstall Vaultwarden, run the following commands:

```bash title="Uninstall Vaultwarden"
sudo docker stack rm vaultwarden
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data files, run the following commands:

```bash title="Remove the data files"
sudo rm /swarm-vol/vaultwarden -rf
```

That's it! You have successfully uninstalled Vaultwarden from AnduinOS.

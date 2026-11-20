# Host Verdaccio

Verdaccio is a lightweight and free private npm proxy registry. It allows you to host your own private npm packages and cache public npm packages locally. Verdaccio is a self-hosted alternative to services like npm Enterprise, Artifactory, and GitHub Packages, giving your team a fast and private package registry.

To host Verdaccio on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/Verdaccio
cd ~/Source/ServiceConfigs/Verdaccio
```

Make sure no other process is taking 4873 port on your machine.

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

port_exist_check 4873
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  verdaccio:
    image: verdaccio/verdaccio
    volumes:
      - verdaccio-storage:/verdaccio/storage
      - verdaccio-config:/verdaccio/conf
    ports:
      - target: 4873
        published: 4873
        protocol: tcp
        mode: host
    deploy:
      resources:
        limits:
          memory: 2G

volumes:
  verdaccio-storage:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/verdaccio/storage
  verdaccio-config:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/verdaccio/config
EOF
sudo mkdir -p /swarm-vol/verdaccio/storage
sudo mkdir -p /swarm-vol/verdaccio/config
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml verdaccio --detach
```

That's it! You have successfully hosted Verdaccio on AnduinOS.

You can access Verdaccio by visiting `http://localhost:4873` in your browser.

To configure your npm client to use this registry, run:

```bash title="Configure npm to use Verdaccio"
npm set registry http://localhost:4873
```

## Uninstall

To uninstall Verdaccio, run the following commands:

```bash title="Uninstall Verdaccio"
sudo docker stack rm verdaccio
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the storage and config files, run the following commands:

```bash title="Remove the storage and config files"
sudo rm /swarm-vol/verdaccio -rf
```

That's it! You have successfully uninstalled Verdaccio from AnduinOS.

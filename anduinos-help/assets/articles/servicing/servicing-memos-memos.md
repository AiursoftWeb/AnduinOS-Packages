# Host Memos

Memos is a privacy-first, lightweight note-taking service. It is an open-source, self-hosted memo hub with knowledge management and social networking. You can write plain text, markdown, and todo lists, and share them easily with others.

To host Memos on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/Memos
cd ~/Source/ServiceConfigs/Memos
```

Make sure no other process is taking 5230 port on your machine.

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

port_exist_check 5230
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  memos:
    image: neosmemo/memos:stable
    volumes:
      - memos-data:/var/opt/memos
    environment:
      - MEMOS_MODE=prod
      - MEMOS_PORT=5230
    ports:
      - target: 5230
        published: 5230
        protocol: tcp
        mode: host
    deploy:
      resources:
        limits:
          memory: 2G

volumes:
  memos-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/memos/data
EOF
sudo mkdir -p /swarm-vol/memos/data
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml memos --detach
```

That's it! You have successfully hosted Memos on AnduinOS.

You can access Memos by visiting `http://localhost:5230` in your browser.

The default admin account is created during the first login at the initial setup page.

## Uninstall

To uninstall Memos, run the following commands:

```bash title="Uninstall Memos"
sudo docker stack rm memos
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data files, run the following commands:

```bash title="Remove the data files"
sudo rm /swarm-vol/memos -rf
```

That's it! You have successfully uninstalled Memos from AnduinOS.

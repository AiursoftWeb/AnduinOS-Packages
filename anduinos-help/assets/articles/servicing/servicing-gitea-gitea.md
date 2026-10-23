# Host Gitea

Gitea is a lightweight self-hosted Git service. It is similar to GitHub, Bitbucket, and Gitlab. Gitea is a community managed lightweight code hosting solution written in Go. It is published under the MIT license.

To host Gitea on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/Gitea
cd ~/Source/ServiceConfigs/Gitea
```

Make sure no other process is taking 3000 and 2222 ports on your machine.

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

port_exist_check 3000
port_exist_check 2222
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  app:
    image: gitea/gitea:latest
    volumes:
      - gitea-data:/data
    ports:
      - target: 3000
        published: 3000
        protocol: tcp
        mode: host
      - target: 22
        published: 2222
        protocol: tcp
        mode: host
    environment:
      - USER_UID=1000
      - USER_GID=1000
      - GITEA__server__SSH_PORT=2222
      - GITEA__server__SSH_LISTEN_PORT=22
    deploy:
      resources:
        limits:
          memory: 4G

volumes:
  gitea-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/gitea/data
EOF
sudo mkdir -p /swarm-vol/gitea/data
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml gitea --detach
```

That's it! You have successfully hosted Gitea on AnduinOS.

You can access Gitea by visiting `http://localhost:3000` in your browser.

The default admin account is created during the first login at the initial setup page.

## Uninstall

To uninstall Gitea, run the following commands:

```bash title="Uninstall Gitea"
sudo docker stack rm gitea
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data files, run the following commands:

```bash title="Remove the data files"
sudo rm /swarm-vol/gitea -rf
```

That's it! You have successfully uninstalled Gitea from AnduinOS.

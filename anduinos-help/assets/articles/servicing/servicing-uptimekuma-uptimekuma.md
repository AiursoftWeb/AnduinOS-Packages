# Host Uptime Kuma

Uptime Kuma is an easy-to-use self-hosted monitoring tool. It monitors your websites, applications, and services and sends notifications when they go down. It provides a beautiful status page and supports a wide range of notification methods.

To host Uptime Kuma on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/UptimeKuma
cd ~/Source/ServiceConfigs/UptimeKuma
```

Make sure no other process is taking 3001 port on your machine.

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

port_exist_check 3001
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  server:
    image: louislam/uptime-kuma:1
    volumes:
      - uptime-kuma:/app/data
    ports:
      - target: 3001
        published: 3001
        protocol: tcp
        mode: host
    environment:
      - NODE_ENV=production
    deploy:
      resources:
        limits:
          memory: 2G

volumes:
  uptime-kuma:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/uptime-kuma/data
EOF
sudo mkdir -p /swarm-vol/uptime-kuma/data
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml uptime-kuma --detach
```

That's it! You have successfully hosted Uptime Kuma on AnduinOS.

You can access Uptime Kuma by visiting `http://localhost:3001` in your browser.

The default admin account is created during the first login at the initial setup page.

## Uninstall

To uninstall Uptime Kuma, run the following commands:

```bash title="Uninstall Uptime Kuma"
sudo docker stack rm uptime-kuma
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data files, run the following commands:

```bash title="Remove the data files"
sudo rm /swarm-vol/uptime-kuma -rf
```

That's it! You have successfully uninstalled Uptime Kuma from AnduinOS.

# Host Grafana

Grafana is an open-source analytics and monitoring platform. It allows you to query, visualize, alert on, and understand your metrics no matter where they are stored. You can create, explore, and share dashboards with your team and promote a data-driven culture. Grafana is commonly used with data sources like Prometheus, InfluxDB, and Elasticsearch.

To host Grafana on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/Grafana
cd ~/Source/ServiceConfigs/Grafana
```

Make sure no other process is taking 3000 port on your machine.

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

port_exist_check 3000
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  grafana:
    image: grafana/grafana:latest
    volumes:
      - grafana-config:/etc/grafana
      - grafana-data:/var/lib/grafana
    environment:
      - TZ=UTC
    ports:
      - target: 3000
        published: 3000
        protocol: tcp
        mode: host
    deploy:
      resources:
        limits:
          memory: 2G

volumes:
  grafana-config:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/grafana/config
  grafana-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/grafana/data
EOF
sudo mkdir -p /swarm-vol/grafana/config
sudo mkdir -p /swarm-vol/grafana/data
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml grafana --detach
```

That's it! You have successfully hosted Grafana on AnduinOS.

You can access Grafana by visiting `http://localhost:3000` in your browser.

The default username is `admin` and the default password is `admin`. You will be prompted to change the password on first login.

## Uninstall

To uninstall Grafana, run the following commands:

```bash title="Uninstall Grafana"
sudo docker stack rm grafana
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data and config files, run the following commands:

```bash title="Remove the data and config files"
sudo rm /swarm-vol/grafana -rf
```

That's it! You have successfully uninstalled Grafana from AnduinOS.

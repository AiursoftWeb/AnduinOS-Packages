# Host Vikunja

Vikunja is a self-hosted open-source to-do list and project management application. It allows you to organize your tasks, projects, and teams in one place. Vikunja is an alternative to proprietary services like Jira, Trello, and Asana, giving you full control over your data.

To host Vikunja on AnduinOS, run the following commands.

First, make sure Docker is installed on your machine. If not, you can install Docker by running the following commands:

```bash title="Install Docker"
sudo apt install -y docker.io
```

Create a new folder to save the service configuration files:

```bash title="Prepare a clean directory"
# Please install Docker first
mkdir -p ~/Source/ServiceConfigs/Vikunja
cd ~/Source/ServiceConfigs/Vikunja
```

Make sure no other process is taking 3456 port on your machine.

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

port_exist_check 3456
```

Then, create a `docker-compose.yml` file with the following content:

```bash title="Create a docker-compose.yml file"
cat << EOF > ./docker-compose.yml
version: '3.9'

services:
  app:
    depends_on:
      - db
    image: vikunja/vikunja:latest
    volumes:
      - vikunja-files:/app/vikunja/files
    environment:
      - VIKUNJA_SERVICE_PUBLICURL=http://localhost:3456
      - VIKUNJA_DATABASE_HOST=db
      - VIKUNJA_DATABASE_PASSWORD=vikunja_password
      - VIKUNJA_DATABASE_TYPE=mysql
      - VIKUNJA_DATABASE_USER=vikunja
      - VIKUNJA_DATABASE_DATABASE=vikunja
    ports:
      - target: 3456
        published: 3456
        protocol: tcp
        mode: host
    networks:
      - internal
    deploy:
      resources:
        limits:
          memory: 4G

  db:
    image: mysql:8
    networks:
      - internal
    volumes:
      - vikunja-db:/var/lib/mysql
    environment:
      - MYSQL_RANDOM_ROOT_PASSWORD=true
      - MYSQL_DATABASE=vikunja
      - MYSQL_USER=vikunja
      - MYSQL_PASSWORD=vikunja_password
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h localhost -u root --silent"]
      start_period: 20s
      interval: 30s
      retries: 5
      timeout: 5s
    deploy:
      resources:
        limits:
          memory: 4G

networks:
  internal:
    driver: overlay

volumes:
  vikunja-files:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/vikunja/files
  vikunja-db:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /swarm-vol/vikunja/db
EOF
sudo mkdir -p /swarm-vol/vikunja/files
sudo mkdir -p /swarm-vol/vikunja/db
```

Then, deploy the service:

```bash title="Deploy the service"
sudo docker swarm init  --advertise-addr $(hostname -I | awk '{print $1}')
sudo docker stack deploy -c docker-compose.yml vikunja --detach
```

That's it! You have successfully hosted Vikunja on AnduinOS.

You can access Vikunja by visiting `http://localhost:3456` in your browser.

The default admin account is created during the first login at the initial setup page.

## Uninstall

To uninstall Vikunja, run the following commands:

```bash title="Uninstall Vikunja"
sudo docker stack rm vikunja
sleep 20 # Wait for the stack to be removed
sudo docker system prune -a --volumes -f # Clean up used volumes and images
```

To also remove the data and database files, run the following commands:

```bash title="Remove the data and database files"
sudo rm /swarm-vol/vikunja -rf
```

That's it! You have successfully uninstalled Vikunja from AnduinOS.

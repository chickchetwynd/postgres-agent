#!/bin/bash

set -e  # exit on error
set -o pipefail

echo "==== Updating and installing prerequisites ===="
sudo apt-get update
sudo apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    software-properties-common

echo "==== Installing Docker ===="
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
sudo add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
   $(lsb_release -cs) \
   stable"

sudo apt-get update
sudo apt-get install -y docker-ce docker-compose-plugin

echo "==== Adding current user to docker group ===="
sudo usermod -aG docker ubuntu || true

echo "==== Creating app directory ===="
mkdir -p $HOME/postgres-agent-server
cd $HOME/postgres-agent-server

echo "==== Downloading compose.yaml from S3 ===="
curl -O https://airfold-postgres-agent-config.s3.us-east-2.amazonaws.com/compose.yaml

echo "==== Starting Docker Compose ===="
sudo docker compose up -d

echo "==== Done! ===="

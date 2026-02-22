#!/bin/bash

# Keycloak Start Script
# Starts a local Keycloak instance with Docker Compose

set -e

echo "🚀 Starting Keycloak..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker first."
    exit 1
fi

# Detect Docker Compose command (v1 or v2)
# Use a function wrapper to handle multi-word commands properly
if command -v docker-compose >/dev/null 2>&1; then
    docker_compose() { docker-compose "$@"; }
    DOCKER_COMPOSE_DISPLAY="docker-compose"
    echo "🐳 Using Docker Compose v1"
elif docker help compose >/dev/null 2>&1; then
    docker_compose() { command docker compose "$@"; }
    DOCKER_COMPOSE_DISPLAY="docker compose"
    echo "🐳 Using Docker Compose v2"
else
    echo "❌ Docker Compose is not installed."
    echo ""
    echo "To install Docker Compose v2 (recommended), run:"
    echo "  mkdir -p ~/.docker/cli-plugins && \\"
    echo "  curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -o ~/.docker/cli-plugins/docker-compose && \\"
    echo "  chmod +x ~/.docker/cli-plugins/docker-compose"
    echo ""
    echo "Or to install Docker Compose v1, run:"
    echo "  sudo curl -sSL \"https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose && \\"
    echo "  sudo chmod +x /usr/local/bin/docker-compose"
    exit 1
fi

# Start Keycloak using detected docker-compose command
echo "🐳 Starting Keycloak..."
docker_compose up -d

# Wait for Keycloak to become ready
echo "⏳ Waiting for Keycloak to become ready..."
echo ""

timeout=120
counter=0

while [ $counter -lt $timeout ]; do
    if curl -s http://localhost:8080/health/ready > /dev/null 2>&1; then
        echo "✅ Keycloak is ready!"
        break
    fi

    # Show recent logs while waiting
    echo "   Still waiting... ($counter/$timeout seconds)"
    echo "   Recent logs:"
    docker logs --tail 3 keycloak-fastmcp 2>/dev/null | sed 's/^/     /' || echo "     (logs not available yet)"
    echo ""

    sleep 5
    counter=$((counter + 5))
done

if [ $counter -ge $timeout ]; then
    echo "❌ Keycloak failed to get ready within $timeout seconds"
    echo "   Check logs with: docker logs -f keycloak-fastmcp"
    exit 1
fi

echo ""
echo "🎉 Keycloak is ready!"
echo ""
echo "Keycloak Admin Console: http://localhost:8080/admin"
echo "  Username: admin"
echo "  Password: admin123"
echo ""
echo "Test User Credentials:"
echo "  Username: testuser"
echo "  Password: password123"
echo ""
echo "Useful commands:"
echo "  • Check Keycloak logs: docker logs -f keycloak-fastmcp"
echo "  • Stop Keycloak: $DOCKER_COMPOSE_DISPLAY down"
echo "  • Reload realm config: $DOCKER_COMPOSE_DISPLAY down -v && $DOCKER_COMPOSE_DISPLAY up -d"
echo ""
echo "⚠️  Note: To apply changes to realm-fastmcp.json, you must stop and remove volumes:"
echo "    $DOCKER_COMPOSE_DISPLAY down -v && $DOCKER_COMPOSE_DISPLAY up -d"
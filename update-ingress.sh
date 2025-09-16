#!/bin/bash
set -eo pipefail

# ======= Configuration =======
SERVICE_FILE="service.yaml"
DEPLOYMENT_FILE="deployment.yaml"


# ====== Get NGINX IP ======
echo " Detecting NGINX Ingress IP address"
NGINX_IP=$(kubectl get svc ingress-nginx-controller -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)

if [ -z "$NGINX_IP" ]; then
  echo " ERROR: NGINX Ingress IP not found!"
  echo "   - Check if NGINX is running: kubectl get pods -n ingress-nginx"
  echo "   - Verify service exists: kubectl get svc -n ingress-nginx"
  exit 1
fi

echo " Detected NGINX IP: $NGINX_IP"

# ========= Generate and Apply Updated Manifest ==========
echo "Updating manifests with current IP..."
sed "s/NGINX_IP_PLACEHOLDER/$NGINX_IP/g" "$SERVICE_FILE" | kubectl apply -f -
sed "s/NGINX_IP_PLACEHOLDER/$NGINX_IP/g" "$DEPLOYMENT_FILE" | kubectl apply -f -


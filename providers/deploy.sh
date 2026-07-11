#!/usr/bin/env bash
# Cloud Run Deployment Script for sach-expense-adjudicator MCP Providers.
#
# This script builds the reusable MCP invoice provider Docker image using Google Cloud Build,
# and then deploys three independent instances to Google Cloud Run (hotel, restaurant, cab)
# using their respective invoice configuration databases.

set -euo pipefail

# 1. Resolve Project Root and Load .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$PROJECT_ROOT/.env" ]; then
    echo "Loading environment configurations from .env..."
    # Export vars, ignoring comments
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
else
    echo "Error: No .env file found at project root ($PROJECT_ROOT)."
    echo "Please create a .env file from .env.example with your GCP_PROJECT_ID configured."
    exit 1
fi

if [ -z "${GCP_PROJECT_ID:-}" ]; then
    echo "Error: GCP_PROJECT_ID is not configured in your .env file."
    exit 1
fi

# Ensure we are in the providers directory
cd "$SCRIPT_DIR"

IMAGE_NAME="gcr.io/${GCP_PROJECT_ID}/mcp-invoice-provider:latest"

echo "--------------------------------------------------------"
echo "Deploying to GCP Project: $GCP_PROJECT_ID"
echo "Docker Image: $IMAGE_NAME"
echo "--------------------------------------------------------"

# 2. Build the Docker Image on Google Cloud Build
echo "Submitting Docker build to Google Cloud Build..."
gcloud builds submit --project="$GCP_PROJECT_ID" --tag="$IMAGE_NAME" .

echo "--------------------------------------------------------"
echo "Docker build completed successfully! Starting Cloud Run deployments..."
echo "--------------------------------------------------------"

# 3. Deploy the Three Cloud Run Instances
providers=("hotel-provider" "restaurant-provider" "cab-provider")
invoice_files=(
    "/app/data/hotel_invoices.json"
    "/app/data/restaurant_invoices.json"
    "/app/data/cab_invoices.json"
)

# Array to store deployed URLs
deployed_urls=()

for i in "${!providers[@]}"; do
    service_name="${providers[$i]}"
    invoice_path="${invoice_files[$i]}"

    echo "Deploying service '$service_name' using configuration: '$invoice_path'..."

    # Deploy to Cloud Run
    gcloud run deploy "$service_name" \
        --project="$GCP_PROJECT_ID" \
        --image="$IMAGE_NAME" \
        --platform="managed" \
        --region="us-central1" \
        --allow-unauthenticated \
        --set-env-vars="INVOICES_FILE_PATH=$invoice_path" \
        --quiet

    # Fetch and save deployed service URL
    url=$(gcloud run services describe "$service_name" --project="$GCP_PROJECT_ID" --platform="managed" --region="us-central1" --format='value(status.url)')
    deployed_urls+=("$url")
    echo "Service '$service_name' successfully deployed at: $url"
    echo "--------------------------------------------------------"
done

# 4. Print Summary instructions
echo "========================================================"
echo "All MCP Provider Services successfully deployed!"
echo "========================================================"
echo "Copy these URLs into your '.env' file:"
echo ""
echo "HOTEL_MCP_ENDPOINT=${deployed_urls[0]}"
echo "RESTAURANT_MCP_ENDPOINT=${deployed_urls[1]}"
echo "CAB_MCP_ENDPOINT=${deployed_urls[2]}"
echo "========================================================"

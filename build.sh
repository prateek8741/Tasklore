#!/usr/bin/env bash
# Build the API container image in Google Cloud (Cloud Build) and push it to Artifact Registry.
# Names come from Terraform, so run `terraform apply` in infra/ first.
# Usage: ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

tf_out() { terraform -chdir=infra output -raw "$1"; }
PROJECT_ID="$(tf_out project_id)"
IMAGE="$(tf_out image_repo)/api"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"

gcloud builds submit backend \
  --project "$PROJECT_ID" \
  --config cloudbuild.yaml \
  --service-account "projects/$PROJECT_ID/serviceAccounts/$(tf_out builder_service_account)" \
  --substitutions "_IMAGE=$IMAGE,_TAG=$TAG"

echo "Pushed $IMAGE:$TAG (also tagged latest)"

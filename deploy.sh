#!/usr/bin/env bash
# Deploy Tasklore code. Infrastructure (services, accounts, permissions) is Terraform's
# job: run `terraform apply` in infra/ first.
# Usage:  ./deploy.sh        API and web app
#         ./deploy.sh api    API only: build the image, roll it out on Cloud Run
#         ./deploy.sh web    web app only: publish frontend/public to Firebase Hosting
set -euo pipefail
cd "$(dirname "$0")"

WHAT="${1:-all}"
tf_out() { terraform -chdir=infra output -raw "$1"; }
PROJECT_ID="$(tf_out project_id)"

if [[ "$WHAT" == "all" || "$WHAT" == "api" ]]; then
  export TAG="$(date +%Y%m%d-%H%M%S)"
  ./build.sh
  echo "==> Rolling out the new image on Cloud Run"
  gcloud run services update "$(tf_out api_service)" \
    --project "$PROJECT_ID" --region "$(tf_out region)" \
    --image "$(tf_out image_repo)/api:$TAG"
fi

if [[ "$WHAT" == "all" || "$WHAT" == "web" ]]; then
  echo "==> Publishing the web app to Firebase Hosting"
  # Video capture calls Cloud Run directly (Hosting stops requests after 60 s). The file exists only
  # during the deploy, so a local run never sends uploads to the live API.
  trap 'rm -f frontend/public/config.js' EXIT
  echo "window.FIRSTRUNG_DIRECT_API = \"$(tf_out api_url)\";" > frontend/public/config.js
  firebase deploy --only hosting --project "$PROJECT_ID"
fi

echo "Done. Web: $(tf_out web_url)  API: $(tf_out api_url)"

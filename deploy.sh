#!/usr/bin/env bash
# Deploy FirstRung code. Infrastructure (services, accounts, permissions) is Terraform's
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
  firebase deploy --only hosting --project "$PROJECT_ID"
fi

echo "Done. API: $(tf_out api_url)"

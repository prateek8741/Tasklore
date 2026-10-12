# Mentor step 1: Artifact Registry, the private store for our container images,
# plus the robot account that Cloud Build uses to build and push them.

resource "google_artifact_registry_repository" "images" {
  repository_id = "firstrung"
  location      = var.region
  format        = "DOCKER"
  description   = "FirstRung container images"

  depends_on = [google_project_service.enabled]
}

locals {
  image_repo = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

# New projects no longer come with a ready-made build account, so we create one
# that can only push images to this registry and write build logs.
resource "google_service_account" "builder" {
  account_id   = "firstrung-builder"
  display_name = "FirstRung image builder (Cloud Build)"

  depends_on = [google_project_service.enabled]
}

resource "google_artifact_registry_repository_iam_member" "builder_push" {
  repository = google_artifact_registry_repository.images.name
  location   = google_artifact_registry_repository.images.location
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.builder.member
}

resource "google_project_iam_member" "builder" {
  for_each = toset([
    "roles/logging.logWriter",    # write build logs
    "roles/storage.objectViewer", # read the source code gcloud uploads for each build
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.builder.member
}

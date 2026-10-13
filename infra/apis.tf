# Google Cloud services FirstRung uses. Turning a service on is free; you pay only for usage.
locals {
  services = [
    "artifactregistry.googleapis.com",   # container image storage
    "cloudbuild.googleapis.com",         # builds container images in the cloud
    "iam.googleapis.com",                # service accounts
    "run.googleapis.com",                # Cloud Run, where the API runs
    "firestore.googleapis.com",          # database
    "firebaserules.googleapis.com",      # Firestore security rules
    "firebase.googleapis.com",           # Firebase project management
    "identitytoolkit.googleapis.com",    # Firebase Authentication
    "secretmanager.googleapis.com",      # stores the Gemini API key
    "generativelanguage.googleapis.com", # Gemini API
    "youtube.googleapis.com",            # YouTube Data API: list playlist videos, lengths, visibility
    "apikeys.googleapis.com",            # create the restricted YouTube API key with gcloud
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.services)
  service  = each.value

  # Keep services on even if this list changes; turning one off can break running parts.
  disable_on_destroy = false
}

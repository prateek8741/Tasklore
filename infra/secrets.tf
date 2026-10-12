# Mentor step 6: Secret Manager holds the Gemini (Google AI Studio) API key.
#
# Terraform creates only the empty secret and the permission to read it.
# The key itself is added from the terminal (see README), so it never appears
# in code, in git, or in Terraform state.
resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

# Only the API's robot account may read the key.
resource "google_secret_manager_secret_iam_member" "api_reads_gemini_key" {
  secret_id = google_secret_manager_secret.gemini_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.api.member
}

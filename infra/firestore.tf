# Mentor step 4: Firestore database, security rules, and the API's permission to use it.

resource "google_firestore_database" "main" {
  name        = "(default)"
  location_id = var.region # permanent: a database's location can never change
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.enabled]
}

# Rules come from ../firestore.rules (deny all direct client access).
resource "google_firebaserules_ruleset" "firestore" {
  source {
    files {
      name    = "firestore.rules"
      content = file("${path.module}/../firestore.rules")
    }
  }

  depends_on = [google_firestore_database.main]
}

resource "google_firebaserules_release" "firestore" {
  name         = "cloud.firestore"
  ruleset_name = google_firebaserules_ruleset.firestore.name

  lifecycle {
    replace_triggered_by = [google_firebaserules_ruleset.firestore]
  }
}

# The API may read and write documents (nothing else).
resource "google_project_iam_member" "api_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = google_service_account.api.member
}

# Mentor step 2: the FirstRung API on Cloud Run.

# The API runs as its own robot account. It starts with no permissions;
# later stages grant exactly what it needs (Firestore, the Gemini key secret).
resource "google_service_account" "api" {
  account_id   = "firstrung-api"
  display_name = "FirstRung API (Cloud Run)"

  depends_on = [google_project_service.enabled]
}

resource "google_cloud_run_v2_service" "api" {
  name                = "firstrung-api" # must match firebase.json
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.api.email
    timeout         = "300s" # for direct calls; requests through Firebase Hosting stop at 60 s

    scaling {
      max_instance_count = 3 # keeps costs predictable
    }

    containers {
      # The first deploy uses the image build.sh pushed as "latest". After that,
      # new images are rolled out with gcloud and Terraform leaves the image alone.
      image = "${local.image_repo}/api:latest"

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        startup_cpu_boost = true # faster cold starts
      }

      env {
        name  = "GEMINI_MODELS"
        value = "gemini-flash-latest,gemini-3-flash-preview"
      }
      env {
        name  = "USE_FIRESTORE"
        value = "1"
      }
      env {
        # Read from Secret Manager when an instance starts; the key is never stored here.
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "YOUTUBE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.youtube_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        # Hackathon demo: everyone holds all roles and switches with "View as". Set "0" for a real team
        # (then admins and managers give out roles on the Team page; see backend/access.py).
        name  = "DEMO_MODE"
        value = var.demo_mode
      }
      env {
        # Always admins (comma-separated Google emails). Not a secret, so it lives here, not in Secret Manager.
        name  = "ADMIN_EMAILS"
        value = var.admin_emails
      }
      env {
        # Every call except /api/health must carry a valid Firebase sign-in token.
        name  = "AUTH_REQUIRED"
        value = "1"
      }
      env {
        name  = "FIREBASE_PROJECT_ID"
        value = var.project_id
      }
      env {
        # Browsers may call the API only from our own sites (plus local dev). The old project-named
        # address only redirects to the new one now, but stays allowed for pages already open.
        name = "CORS_ORIGINS"
        value = join(",", [
          "https://${var.site_id}.web.app", "https://${var.site_id}.firebaseapp.com",
          "https://${var.project_id}.web.app", "https://${var.project_id}.firebaseapp.com",
          "http://localhost:8080",
        ])
      }
    }
  }

  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.api_reads_gemini_key,
    google_secret_manager_secret_iam_member.api_reads_youtube_key,
  ]
}

# Anyone on the internet may reach the API (Firebase Hosting needs this).
# The API itself checks who is signed in; that is stage 4.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

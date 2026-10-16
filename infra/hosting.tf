# The web app's address: https://tasklore.web.app (the app was called FirstRung until 2026-10-16).
#
# A project's own Hosting site is named after the project ID (firstrung-993fd.web.app), and a project ID
# can never change. So the app gets a second site with its own name; the old address stays and redirects
# here (see firebase.json), so earlier links keep working. Site names are global across all of Firebase.
resource "google_firebase_hosting_site" "app" {
  provider = google-beta
  site_id  = var.site_id
  app_id   = google_firebase_web_app.web.app_id # its /__/firebase/init.json serves this web app's config
  # A deleted site name can't be reused right away; never let Terraform remove it by accident.
  deletion_policy = "PREVENT"

  depends_on = [google_project_service.enabled]
}

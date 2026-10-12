# Mentor step 3: Firebase Authentication.
#
# Registers the web app with Firebase. Firebase Hosting then serves its public
# config at /__/firebase/init.json, so no keys are written into our code.
#
# Turning on the Google sign-in provider is one click in the Firebase console
# (Authentication > Sign-in method > Google). Doing it here would put an OAuth
# client secret into Terraform state.
resource "google_firebase_web_app" "web" {
  provider        = google-beta
  display_name    = "FirstRung web"
  deletion_policy = "DELETE"

  depends_on = [google_project_service.enabled]
}

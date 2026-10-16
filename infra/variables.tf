variable "project_id" {
  description = "Google Cloud project ID (the same project is the Firebase project)."
  type        = string
}

variable "site_id" {
  description = "Firebase Hosting site name: the web app is served at https://<site_id>.web.app. Must match firebase.json."
  type        = string
  default     = "tasklore"
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Firestore. Must match firebase.json."
  type        = string
  default     = "asia-south1" # Mumbai
}

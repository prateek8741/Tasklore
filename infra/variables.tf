variable "project_id" {
  description = "Google Cloud project ID (the same project is the Firebase project)."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Firestore. Must match firebase.json."
  type        = string
  default     = "asia-south1" # Mumbai
}

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

variable "admin_emails" {
  description = "Comma-separated Google account emails that are always Tasklore admins (they pick managers and admins)."
  type        = string
  default     = ""
}

variable "demo_mode" {
  description = "\"1\": everyone who signs in can try every role (hackathon judging). \"0\": only roles given on the Team page count."
  type        = string
  default     = "1"
  validation {
    condition     = contains(["0", "1"], var.demo_mode)
    error_message = "demo_mode must be \"0\" or \"1\"."
  }
}

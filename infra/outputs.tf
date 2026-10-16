output "project_id" {
  value = var.project_id
}

output "image_repo" {
  description = "Where container images are pushed."
  value       = local.image_repo
}

output "builder_service_account" {
  value = google_service_account.builder.email
}

output "region" {
  value = var.region
}

output "api_service" {
  value = google_cloud_run_v2_service.api.name
}

output "web_url" {
  description = "Where people open the web app."
  value       = google_firebase_hosting_site.app.default_url
}

output "api_url" {
  description = "Public URL of the API on Cloud Run."
  value       = google_cloud_run_v2_service.api.uri
}

# The app settings a deployment may set, and their rules. Both roots call this,
# so infra/data/'s plan rejects bad settings before it creates the data volume
# and Elastic IP, and infra/'s plan rejects them before the server changes.

variable "settings" {
  description = "The app's settings, as in app_settings in infra/terraform.tfvars."
  type        = map(number)

  validation {
    # The settings compose.yaml forwards to the app; tests/test_compose.py
    # keeps the two lists the same.
    condition = alltrue([
      for name in keys(var.settings) : contains([
        "GROUNDLINE_CHECK_TIMEOUT_SECONDS",
        "GROUNDLINE_MODEL_MAX_ATTEMPTS",
        "GROUNDLINE_MAX_BODY_BYTES",
        "GROUNDLINE_MAX_SOURCE_CHARS",
        "GROUNDLINE_MAX_SUMMARY_CHARS",
      ], name)
    ])
    error_message = "app_settings can set GROUNDLINE_CHECK_TIMEOUT_SECONDS, GROUNDLINE_MODEL_MAX_ATTEMPTS, GROUNDLINE_MAX_BODY_BYTES, GROUNDLINE_MAX_SOURCE_CHARS and GROUNDLINE_MAX_SUMMARY_CHARS."
  }
  validation {
    condition     = alltrue([for value in values(var.settings) : value > 0])
    error_message = "Each app setting must be above 0."
  }
  validation {
    # Only the check time limit can be a fraction of a second.
    condition = alltrue([
      for name, value in var.settings :
      name == "GROUNDLINE_CHECK_TIMEOUT_SECONDS" || floor(value) == value
    ])
    error_message = "GROUNDLINE_MODEL_MAX_ATTEMPTS and the request size limits must be whole numbers."
  }
}

output "settings" {
  description = "The settings, once they pass the rules above."
  value       = var.settings
}

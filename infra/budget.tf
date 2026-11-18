# Emails budget_alert_email when the AWS project's actual cost this month
# passes half of budget_amount and all of it: by default $2.50 and $5 for a
# disposable deployment, to catch a server someone forgot to destroy (R2), and
# $17.50 and $35 for a lasting one, about twice what it costs.
#
# It covers the whole AWS project: limiting it to the app tag needs an
# account-level billing setting. Each deployment has its own, named after
# deployment_name, so everyone sharing an AWS project hears when the project's
# cost runs high, whoever's server caused it. Credits and refunds are left out, since on the
# Free plan credits bring the cost to $0 and the alert would never fire.

locals {
  budget_amount = coalesce(var.budget_amount, var.lifetime == "lasting" ? 35 : 5)
}

resource "aws_budgets_budget" "monthly" {
  name         = "groundline-monthly-${var.deployment_name}"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  limit_amount = tostring(local.budget_amount)
  limit_unit   = "USD"

  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = [50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_alert_email]
    }
  }
}

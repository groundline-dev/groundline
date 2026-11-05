# The budget alert that catches a forgotten server.
# A mock AWS provider stands in for AWS, so these tests need no credentials
# and create nothing. Run with: terraform -chdir=infra test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}

variables {
  region             = "eu-north-1"
  budget_alert_email = "alerts@example.com"
  tester_cidr        = "203.0.113.7/32"
  git_ref            = "0123456789abcdef0123456789abcdef01234567"
}

run "the_budget_is_five_dollars_of_cost_a_month" {
  command = plan

  assert {
    condition = (
      aws_budgets_budget.monthly.budget_type == "COST"
      && aws_budgets_budget.monthly.time_unit == "MONTHLY"
      && tonumber(aws_budgets_budget.monthly.limit_amount) == 5
      && aws_budgets_budget.monthly.limit_unit == "USD"
    )
    error_message = "The budget should be 5 USD of cost a month."
  }
  assert {
    # On the Free plan, credits bring the cost to $0, so the alert would never fire.
    condition = (
      aws_budgets_budget.monthly.cost_types[0].include_credit == false
      && aws_budgets_budget.monthly.cost_types[0].include_refund == false
    )
    error_message = "The budget should leave out credits and refunds."
  }
  assert {
    # Filtering by the project tag needs an account-level billing setting.
    condition     = length(aws_budgets_budget.monthly.cost_filter) == 0
    error_message = "The budget should cover the whole AWS project, with no cost filter."
  }
}

run "the_budget_emails_at_half_and_all_of_actual_cost" {
  command = plan

  assert {
    condition     = toset([for n in aws_budgets_budget.monthly.notification : n.threshold]) == toset([50, 100])
    error_message = "The budget should notify at 50% and 100%."
  }
  assert {
    condition = alltrue([
      for n in aws_budgets_budget.monthly.notification :
      n.threshold_type == "PERCENTAGE"
      && n.notification_type == "ACTUAL"
      && n.comparison_operator == "GREATER_THAN"
      && n.subscriber_email_addresses == toset(["alerts@example.com"])
    ])
    error_message = "Each notification should email budget_alert_email when actual cost passes its percentage."
  }
}

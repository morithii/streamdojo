#!/usr/bin/env bash
# =============================================================================
# Creates demo StreamDojo accounts for reviewers - no real email needed.
#   - dummy addresses at example.com (a reserved domain: can never reach a real person)
#   - no invite email (SUPPRESS); a strong random PERMANENT password is set instead
#   - added to learners, educators and creators, so they can see every part of the app
# Passwords are printed here in your terminal only: send each one privately.
# Run it again to give someone a NEW password (the account itself is kept).
#
# Usage:   bash scripts/create_demo_accounts.sh
# Needs:   the AWS CLI signed in   (aws login --profile streamdojo)
# =============================================================================
set -euo pipefail

PROFILE="${PROFILE:-streamdojo}"
REGION="eu-west-2"
STACK="streamdojo-dev"
GROUPS_TO_ADD=(learners educators creators)

aws_() { aws "$@" --profile "$PROFILE" --region "$REGION"; }

# The user pool's ID, from the stack outputs
POOL=$(aws_ cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
echo "User pool: $POOL"
echo

# 14 random letters/digits, plus a fixed tail so the pool's rules are always met
# (12+ characters, upper case, lower case, a number and a symbol)
new_password() {
  echo "Dojo-$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | cut -c1-14)-7k!"
}

create() {
  local email="$1" name="$2" password
  password=$(new_password)

  if aws_ cognito-idp admin-get-user --user-pool-id "$POOL" --username "$email" >/dev/null 2>&1; then
    echo "$name: account already exists - setting a new password"
  else
    aws_ cognito-idp admin-create-user --user-pool-id "$POOL" --username "$email" \
      --user-attributes Name=email,Value="$email" Name=email_verified,Value=true Name=name,Value="$name" \
      --message-action SUPPRESS >/dev/null                  # no invite email
    echo "$name: account created"
  fi

  # Permanent password: no "change your password" step at first sign-in
  aws_ cognito-idp admin-set-user-password --user-pool-id "$POOL" --username "$email" \
    --password "$password" --permanent

  for group in "${GROUPS_TO_ADD[@]}"; do                     # adding twice is harmless
    aws_ cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username "$email" --group-name "$group"
  done

  echo "  Email:    $email"
  echo "  Password: $password"
  echo "  Roles:    ${GROUPS_TO_ADD[*]}"
  echo
}

create "melissa.demo@example.com" "Melissa"
create "lefteris.demo@example.com" "Lefteris"

echo "Sign in at: $(aws_ cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text)"

# Recipe Stack SSO Configuration

Guide for configuring Authentik SSO for Mealie and Bar Assistant, plus OpenAI integration for Mealie.

## Overview

This guide configures:
1. **Mealie** - OIDC/SSO authentication via Authentik with group-based access control
2. **Bar Assistant** - Authentik-native SSO integration
3. **Mealie** - OpenAI integration for recipe parsing and image-to-recipe features

> **Authentik objects are Terraform-managed.** The Mealie and Bar Assistant
> OAuth2 providers, applications, and the `mealie-users`/`mealie-admins` groups
> are codified in `terraform/authentik/` (`providers_oauth2.tf`,
> `applications.tf`, `groups.tf`) and changed via a supervised `terraform apply`
> — **not the Authentik UI** ([docs/40-authentik-terraform.md](40-authentik-terraform.md)).
> Creating these objects by hand in the UI produces drift Terraform will revert
> on the next apply. The UI walkthroughs below are retained only as a reference
> for the exact values (redirect URIs, scopes, client credentials, env vars);
> make the actual provider/app/group changes in the `.tf` files.

## Prerequisites

- Authentik running at `auth.ericsweiss.com`
- Mealie running at `food.esweiss.com` / `food.ericsweiss.com`
- Bar Assistant running at `bar.esweiss.com` / `bar.ericsweiss.com`
- OpenAI account with API access (Tier 1 or higher - requires $5+ deposit)
- 1Password CLI configured and signed in (`eval $(op signin)`)

---

## Part 1: Create 1Password Items

Create the following items in your **Homelab** vault:

### 1. Mealie SSO (type: Password)
- **Item name**: `Mealie SSO`
- **Fields** (leave empty for now - will populate after creating Authentik provider):
  - `oidc-client-id` - Client ID from Authentik
  - `oidc-client-secret` - Client Secret from Authentik

### 2. Bar Assistant SSO (type: Password)
- **Item name**: `Bar Assistant SSO`
- **Fields** (leave empty for now - will populate after creating Authentik provider):
  - `authentik-client-id` - Client ID from Authentik
  - `authentik-client-secret` - Client Secret from Authentik

### 3. OpenAI API Key (type: Password)
- **Item name**: `OpenAI API Key`
- **Fields**:
  - `api-key` - Your OpenAI API key (starts with `sk-...`)

**How to get OpenAI API key:**
1. Go to https://platform.openai.com
2. Sign in or create account
3. Add $5+ deposit to access Tier 1 (required for Mealie)
4. Navigate to API Keys section
5. Create new API key and copy it
6. Paste into 1Password field `api-key`

---

## Part 2: Create Authentik User Groups

1. Log into Authentik at https://auth.ericsweiss.com
2. Navigate to **Directory → Groups**
3. Click **Create**

### Create `mealie-users` group
- **Name**: `mealie-users`
- **Parent**: (leave blank)
- Click **Create**
- Click **Add existing user** and add users who should have access to Mealie

### Create `mealie-admins` group
- **Name**: `mealie-admins`
- **Parent**: (leave blank)
- Click **Create**
- Click **Add existing user** and add users who should be Mealie administrators

**Note**: Users in `mealie-admins` will have full administrative access to Mealie. Regular users should only be in `mealie-users`.

---

## Part 3: Configure Mealie OAuth2 Provider

1. Navigate to **Applications → Providers**
2. Click **Create**
3. Select **OAuth2/OpenID Provider**
4. Configure the provider:

| Field | Value |
|-------|-------|
| **Name** | `Mealie` |
| **Authorization flow** | `default-authorization-flow` (implicit-consent) |
| **Protocol settings** | |
| **Client type** | `Confidential` |
| **Client ID** | (auto-generated - **COPY THIS**) |
| **Client Secret** | (auto-generated - **COPY THIS**) |
| **Redirect URIs/Origins (Regex)** | See below |
| **Signing Key** | Select any available certificate |
| **Advanced protocol settings** | |
| **Scopes** | `openid`, `email`, `profile` |

**Redirect URIs/Origins (Regex)**:
```
https://food\.esweiss\.com/login(\?direct=1)?$
https://food\.ericsweiss\.com/login(\?direct=1)?$
```

5. Click **Finish**
6. **IMPORTANT**: Copy the Client ID and Client Secret to 1Password:
   - Open `Mealie SSO` item in 1Password
   - Paste Client ID into `oidc-client-id` field
   - Paste Client Secret into `oidc-client-secret` field
   - Save the item

---

## Part 4: Create Mealie Application

1. Navigate to **Applications → Applications**
2. Click **Create**
3. Configure the application:

| Field | Value |
|-------|-------|
| **Name** | `Mealie` |
| **Slug** | `food` |
| **Group** | (leave blank or select if using groups) |
| **Provider** | `Mealie` (select the provider created above) |
| **Launch URL** | `https://food.esweiss.com` |
| **Icon** | (optional - upload Mealie logo) |

4. Click **Create**

---

## Part 5: Configure Bar Assistant OAuth2 Provider

1. Navigate to **Applications → Providers**
2. Click **Create**
3. Select **OAuth2/OpenID Provider**
4. Configure the provider:

| Field | Value |
|-------|-------|
| **Name** | `Bar Assistant` |
| **Authorization flow** | `default-authorization-flow` (implicit-consent) |
| **Protocol settings** | |
| **Client type** | `Confidential` |
| **Client ID** | (auto-generated - **COPY THIS**) |
| **Client Secret** | (auto-generated - **COPY THIS**) |
| **Redirect URIs/Origins (Regex)** | See below |
| **Signing Key** | Select any available certificate |
| **Advanced protocol settings** | |
| **Scopes** | `openid`, `email`, `profile` |

**Redirect URIs/Origins (Regex)**:
```
https://bar\.(es|ericsweiss)\.com/oauth/callback$
```

5. Click **Finish**
6. **IMPORTANT**: Copy the Client ID and Client Secret to 1Password:
   - Open `Bar Assistant SSO` item in 1Password
   - Paste Client ID into `authentik-client-id` field
   - Paste Client Secret into `authentik-client-secret` field
   - Save the item

---

## Part 6: Create Bar Assistant Application

1. Navigate to **Applications → Applications**
2. Click **Create**
3. Configure the application:

| Field | Value |
|-------|-------|
| **Name** | `Bar Assistant` |
| **Slug** | `bar-assistant` |
| **Group** | (leave blank or select if using groups) |
| **Provider** | `Bar Assistant` (select the provider created above) |
| **Launch URL** | `https://bar.esweiss.com` |
| **Icon** | (optional - upload Bar Assistant logo) |

4. Click **Create**

---

## Part 7: Trigger ExternalSecret Refresh

Recipe secrets live in the single ExternalSecret in
`kubernetes/apps/recipes/`:

- **`externalsecret.yaml`** (`recipes-secrets`) -- required credentials: DB
  password, SSO client IDs/secrets, meilisearch master key, and SMTP relay
  auth (username + password from the "SMTP Relay Auth" 1Password item).

The OpenAI key is NOT ESO-synced — it is configured in the Mealie UI under
Settings > AI (see Part 8). See `docs/22-recipes-deployment.md` for the full
field-by-field breakdown.

Once the values above are in 1Password, there's no kubectl or helm step — just
trigger ESO to pick them up (otherwise it refreshes on its own 24h interval):

```bash
# Ensure 1Password is signed in (only if you want to sanity-check locally)
eval $(op signin)
op read 'op://Homelab/Mealie SSO/oidc-client-id'
op read 'op://Homelab/Mealie SSO/oidc-client-secret'
op read 'op://Homelab/Bar Assistant SSO/authentik-client-id'
op read 'op://Homelab/Bar Assistant SSO/authentik-client-secret'

# Force ExternalSecret refresh + restart Mealie and Bar Assistant
task flux:rotate-secret -- recipes
```

This:
1. Triggers `ExternalSecret/recipes-secrets` to re-sync from 1Password
2. Waits for `SecretSynced: True`
3. Restarts Mealie and Bar Assistant Deployments so they read the new env values

If you're doing first-time setup (app hasn't been deployed yet), just commit the
ExternalSecret YAML and let Flux create everything:

```bash
git add kubernetes/apps/recipes/externalsecret.yaml
git commit -m "Add recipes ExternalSecret" && git push
task flux:reconcile
```

---

## Part 8: Testing

### Test Mealie SSO

1. Open https://food.esweiss.com
2. You should see an **"Authentik"** login button
3. Click it and verify redirect to Authentik
4. Log in with an Authentik user (in `mealie-users` or `mealie-admins` group)
5. Verify redirect back to Mealie and automatic login
6. Check user permissions:
   - Users in `mealie-admins` should have admin access
   - Users in only `mealie-users` should have regular user access

**Troubleshooting Mealie SSO:**
```bash
# Check Mealie logs
task recipes:logs APP=mealie

# Verify environment variables are set
kubectl exec -n recipes deployment/mealie -- env | grep OIDC

# Common issues:
# - "Invalid redirect URI" → Check Authentik provider redirect URIs
# - "Invalid client" → Verify client ID/secret in 1Password and secrets
# - User can't access → Add user to mealie-users or mealie-admins group
```

### Test Bar Assistant SSO

1. Open https://bar.esweiss.com
2. You should see a **"Login with Authentik"** or similar SSO option
3. Click it and verify redirect to Authentik
4. Log in with an Authentik user
5. Verify redirect back to Bar Assistant and automatic login

**Troubleshooting Bar Assistant SSO:**
```bash
# Check Bar Assistant logs
task recipes:logs APP=bar-assistant

# Verify environment variables are set
kubectl exec -n recipes deployment/bar-assistant -- env | grep AUTHENTIK

# Common issues:
# - "Invalid redirect URI" → Check Authentik provider redirect URIs
# - "Invalid client" → Verify client ID/secret in 1Password and secrets
```

### Configure + Test Mealie OpenAI Integration

Since Mealie 3.x, AI provider config lives in the Mealie database, not env
vars. Set the key in-app first:

1. Log into Mealie as an admin
2. Go to **Settings > AI** and add an OpenAI provider with the API key from
   the 1Password item `OpenAI API Key` (field `api-key`)

Then test:

1. Log into Mealie
2. **Test URL Import:**
   - Click **"Create Recipe"**
   - Select **"Import from URL"**
   - Paste a recipe URL (e.g., from Serious Eats, NYT Cooking, etc.)
   - Verify OpenAI parses the recipe correctly

3. **Test Image Import (if enabled):**
   - Click **"Create Recipe"**
   - Select **"Import from Image"**
   - Upload a photo of a recipe or screenshot
   - Verify OpenAI extracts recipe information from the image

**Troubleshooting OpenAI:**
```bash
# Check Mealie logs for OpenAI errors
task recipes:logs APP=mealie | grep -i openai

# Common issues:
# - "Invalid API key" → Re-enter the key under Settings > AI (compare against
#   the 1Password item); OPENAI_* env vars are ignored by Mealie 3.x
# - "Insufficient quota" → Add more credits to OpenAI account
# - "Rate limit exceeded" → Wait or upgrade OpenAI tier
```

### Test Bar Assistant Email

1. Log into Bar Assistant
2. **Method 1: Password Reset**
   - Log out
   - Click **"Forgot Password"**
   - Enter your email address
   - Check your email for password reset link

2. **Method 2: Check logs after any email-triggering action**
   ```bash
   task recipes:logs APP=bar-assistant | grep -i mail
   ```

**Troubleshooting Email:**
```bash
# Check Bar Assistant mail configuration
kubectl exec -n recipes deployment/bar-assistant -- env | grep MAIL

# Check SMTP relay logs on smtp-relay host
ssh smtp-relay.esweiss.com
sudo journalctl -u postfix -f
```

---

## Configuration Summary

### Mealie Environment Variables (Configured)

| Variable | Value | Source |
|----------|-------|--------|
| `OIDC_AUTH_ENABLED` | `true` | Static |
| `OIDC_PROVIDER_NAME` | `authentik` | Static |
| `OIDC_CONFIGURATION_URL` | `https://auth.ericsweiss.com/application/o/food/.well-known/openid-configuration` | Static |
| `OIDC_CLIENT_ID` | (from secret) | 1Password: `Mealie SSO/oidc-client-id` |
| `OIDC_CLIENT_SECRET` | (from secret) | 1Password: `Mealie SSO/oidc-client-secret` |
| `OIDC_SIGNUP_ENABLED` | `true` | Static |
| `OIDC_USER_GROUP` | `mealie-users` | Static |
| `OIDC_ADMIN_GROUP` | `mealie-admins` | Static |
| `OIDC_AUTO_REDIRECT` | `false` | Static |
| `OIDC_REMEMBER_ME` | `true` | Static |

OpenAI is **not** configured via env vars: Mealie 3.x ignores the legacy
`OPENAI_*` variables and reads AI provider config from its database. Set the
key in-app under Settings > AI (stored in 1Password item `OpenAI API Key`).

### Bar Assistant Environment Variables (Configured)

| Variable | Value | Source |
|----------|-------|--------|
| `AUTHENTIK_BASE_URL` | `https://auth.ericsweiss.com` | Static |
| `AUTHENTIK_CLIENT_ID` | (from secret) | 1Password: `Bar Assistant SSO/authentik-client-id` |
| `AUTHENTIK_CLIENT_SECRET` | (from secret) | 1Password: `Bar Assistant SSO/authentik-client-secret` |
| `AUTHENTIK_REDIRECT_URI` | `https://bar.ericsweiss.com/oauth/callback` | Static |

---

## Security Notes

1. **Client Secrets**: Keep OAuth2 client secrets secure in 1Password. Never commit to git.
2. **OpenAI API Key**: Monitor usage at https://platform.openai.com/usage to avoid unexpected costs.
3. **Group Membership**: Regularly review Authentik group memberships to ensure proper access control.
4. **SSL/TLS**: All authentication flows require HTTPS (already configured via Traefik + cert-manager).

---

## Cost Considerations

### OpenAI API Costs

Mealie's OpenAI integration uses the following features:

| Feature | Model Used | Approx. Cost | Notes |
|---------|------------|--------------|-------|
| **URL Recipe Import** | GPT-4o (default) or GPT-4o-mini | ~$0.01-0.05 per recipe | Parses HTML/text into structured recipe |
| **Image-to-Recipe** | GPT-4o Vision | ~$0.10-0.30 per image | Extracts recipe from photos (higher cost) |

**Cost Management:**
- Start with image services enabled to test
- Monitor usage at https://platform.openai.com/usage
- Set usage limits in OpenAI account settings
- If costs are too high, disable image services in the Mealie UI (Settings > AI)

**Example monthly costs:**
- 20 URL imports/month: ~$0.20-1.00
- 10 image imports/month: ~$1.00-3.00
- Total: ~$1.20-4.00/month (very reasonable for the convenience)

---

## Maintenance

### Rotating OAuth2 Client Secrets

If you need to rotate secrets:

1. **In Authentik:**
   - Go to the provider (Mealie or Bar Assistant)
   - Edit and generate new Client Secret
   - Copy the new secret

2. **In 1Password:**
   - Update the appropriate secret field
   - Save the item

3. **Refresh ExternalSecret + restart consumers:**
   ```bash
   task flux:rotate-secret -- recipes
   ```

### Updating OpenAI API Key

If you need to rotate the OpenAI API key:

1. **In OpenAI:**
   - Go to https://platform.openai.com/api-keys
   - Revoke old key
   - Create new key

2. **In 1Password:**
   - Update `OpenAI API Key/api-key` field
   - Save the item

3. **In Mealie:**
   - Settings > AI — replace the key on the OpenAI provider (the key is read
     from the Mealie database, not from a Kubernetes Secret)

---

## References

- **Mealie OIDC Documentation**: https://docs.mealie.io/documentation/getting-started/authentication/oidc/
- **Mealie OpenAI Documentation**: https://docs.mealie.io/documentation/getting-started/installation/open-ai/
- **Bar Assistant SSO Documentation**: https://docs.barassistant.app/setup/sso/
- **Authentik Mealie Integration**: https://integrations.goauthentik.io/documentation/mealie/
- **OpenAI Platform**: https://platform.openai.com/

---

## Next Steps

Once SSO is working:

1. **Password login is already disabled**:
   - Both Mealie and Bar Assistant have password-based login disabled by default
   - Mealie: `ALLOW_PASSWORD_LOGIN: "false"` in `mealie.yaml`
   - Bar Assistant: `ENABLE_PASSWORD_LOGIN: "false"` in `bar-assistant.yaml`
   - All users must authenticate through Authentik SSO

2. **Configure other Authentik integrations**:
   - Consider adding Sonarr, Radarr, Prowlarr, etc. to Authentik

3. **Monitor usage**:
   - Check Mealie usage of OpenAI at https://platform.openai.com/usage
   - Review Authentik audit logs for SSO logins

4. **Backup**:
   - Ensure 1Password vault is backed up (automatic with 1Password)
   - Export Authentik configuration periodically

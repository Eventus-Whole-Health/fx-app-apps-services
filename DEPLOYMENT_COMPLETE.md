# Deployment Complete: fx-app-apps-services

**Date:** February 15, 2026  
**Status:** ✅ Successfully Deployed

## Deployed Resources

### Function App
- **Name:** `fx-app-apps-services`
- **Resource Group:** `rg-keystone-platform`
- **URL:** https://fx-app-apps-services.azurewebsites.net
- **Runtime:** Python 3.11
- **Plan:** `keystone-platform-asp` (Keystone ASP - unlimited timeout)
- **State:** Running
- **Storage Account:** `fxappsstorage` (shared)

### Managed Identity
- **Type:** System-Assigned
- **Principal ID:** `0990c1cb-e153-416b-9426-a4669d89d5bb`
- **Permissions:** Key Vault Secrets User on `eventus-apps`

### Application Insights
- **Resource:** `app-insights-master` (via Key Vault reference)
- **Instrumentation Key:** `5a671dd0-160f-4777-9a1c-e98afb1b419b`
- **Resource Group:** `rg-keystone-platform`
- **Shared with:** fx-app-pims-services, fx-app-data-services, fx-app-ai-scribing-services

### Configured Settings (19 total)
✅ APP_NAME: fx-app-apps-services  
✅ APP_VERSION: 1.0.0  
✅ ENVIRONMENT: production  
✅ AZURE_REGION: eastus2  
✅ SEQ_SERVER_URL: http://apps-seq-instance.eastus2.azurecontainer.io:5341  
✅ SEQ_API_KEY: (Key Vault reference)  
✅ SQL_EXECUTOR_URL: https://fx-app-data-services.azurewebsites.net/api/sql-executor  
✅ SQL_EXECUTOR_SCOPE: api://fx-app-data-services/.default  
✅ SQL_EXECUTOR_SERVER: apps  
✅ SQL_EXECUTOR_CLIENT_ID: (Key Vault reference)  
✅ SQL_EXECUTOR_CLIENT_SECRET: (Key Vault reference)  
✅ SQL_EXECUTOR_TENANT_ID: (Key Vault reference)  
✅ APPLICATIONINSIGHTS_CONNECTION_STRING: (Key Vault reference to app-insights-master)

## Next Steps

### 1. Deploy Code via GitHub Actions

Push the code to trigger automatic deployment:

```bash
cd /Users/jgilpatrick/Library/CloudStorage/OneDrive-EventusWholeHealth/Development/active
git add fx-app-apps-services/
git commit -m "Deploy fx-app-apps-services infrastructure with unlimited timeout"
git push origin main
```

GitHub Actions will automatically deploy via `.github/workflows/main_fx-app-apps-services.yml`

### 2. Verify Timer is Disabled

After code deployment, verify the timer trigger schedule:

```bash
# In Azure Portal: fx-app-apps-services → Functions → scheduler_timer
# Schedule should be: "0 0 0 0 * *" (impossible date - Day 0)
```

### 3. Test Endpoints Locally (Optional)

Before production testing:

```bash
# Start function app locally
cd fx-app-apps-services
python -m venv ~/venv/fx-app-apps-services
source ~/venv/fx-app-apps-services/bin/activate
pip install -r requirements.txt
func start
```

### 4. Manual Trigger Test (After Deployment)

```bash
curl -X POST https://fx-app-apps-services.azurewebsites.net/api/scheduler/manual-trigger \
  -H "Content-Type: application/json" \
  -d '{"force_service_ids": [1]}'
```

### 5. Verify Logging

- **Seq:** `~/.dotnet/tools/seqcli search --filter="AppName = 'fx-app-apps-services'" -s http://apps-seq-instance.eastus2.azurecontainer.io --no-websockets`
- **SQL:** Check `jgilpatrick.apps_master_services_log` for entries
- **App Insights:** https://portal.azure.com → app-insights-master → Logs

## Testing Checklist

After code deployment, verify:

- [ ] Function app appears in Azure Portal
- [ ] Timer trigger is disabled (Day 0 impossible schedule)
- [ ] Status endpoint returns data: `https://fx-app-apps-services.azurewebsites.net/api/status/{log_id}`
- [ ] Manual trigger executes: `POST /api/scheduler/manual-trigger`
- [ ] Long-running services (15+ min) complete without timeout errors
- [ ] Seq logs show no timeout warnings
- [ ] SQL master services log contains entries
- [ ] App Insights shows telemetry in app-insights-master

## Key Information

- **Keystone Platform Resource Group:** `rg-keystone-platform`
- **Shared App Service Plan:** `keystone-platform-asp` (unlimited timeout)
- **Shared Storage:** `fxappsstorage`
- **Shared Application Insights:** `app-insights-master`
- **Key Vault:** `eventus-apps` (with RBAC authorization)
- **Cost Allocation:** Function app runs on shared Keystone platform resources (no additional compute costs)

## Rollback Plan

If issues arise:

```bash
# Delete function app (keeps resource group and dependencies intact)
az functionapp delete --name fx-app-apps-services --resource-group rg-keystone-platform --yes
```

Then recreate with corrected configuration.

## Troubleshooting

**Issue:** Settings show null values in portal  
**Solution:** Wait 5-10 minutes for settings to propagate, then refresh browser

**Issue:** Can't access Key Vault secrets  
**Solution:** Verify managed identity has "Key Vault Secrets User" role on eventus-apps vault

**Issue:** Application Insights not logging  
**Solution:** Verify `APPLICATIONINSIGHTS_CONNECTION_STRING` points to app-insights-master via Key Vault

## Migration Phase Status

✅ **Phase 2 Complete:** fx-app-apps-services deployed with:
- Unlimited timeout (no TimeoutTracker)
- Timer trigger disabled for safe testing
- Shared app-insights-master resource
- Simplified scheduler (1,307 lines, down from 1,471)

**Next:** Phase 3 - Migrate other fx apps to distributed status endpoints  
**Then:** Phase 4 - Enable timer, monitor, decommission apps_services

# ATCS Structural Audit

Full structural audit of the VPN Telegram bot subscription lifecycle, Xray integration, and deployment safety.

## Documents

| Document | Description |
|----------|-------------|
| [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md) | Component responsibilities, UUID generation, Xray call sites |
| [SUBSCRIPTION_FLOW_DIAGRAM.md](SUBSCRIPTION_FLOW_DIAGRAM.md) | Payment → subscription → activation flow |
| [XRAY_INTEGRATION_ANALYSIS.md](XRAY_INTEGRATION_ANALYSIS.md) | UUID lifecycle, orphan/ghost risks |
| [RACE_CONDITION_REPORT.md](RACE_CONDITION_REPORT.md) | Race conditions and double-activation risks |
| [TRANSACTION_SAFETY_REPORT.md](TRANSACTION_SAFETY_REPORT.md) | External API in TX, FOR UPDATE usage |
| [INFRA_DEPLOYMENT_RISK.md](INFRA_DEPLOYMENT_RISK.md) | Multi-instance, restart, webhook timeout |
| [PAYMENT_VALIDATION.md](PAYMENT_VALIDATION.md) | Idempotency, provider_invoice_id, TTL |
| [FINAL_RISK_SCORE.md](FINAL_RISK_SCORE.md) | Risk score, required fixes (P0–P2) |
| [SAFE_DEPLOY_CHECKLIST.md](SAFE_DEPLOY_CHECKLIST.md) | Pre/post-deploy validation |

## Summary

- **Risk score:** 6.5/10
- **Critical:** Activation worker race → orphan UUIDs; external API inside DB transactions
- **Recommendation:** Apply P0 fixes before scaling; run single replica until P1 addressed

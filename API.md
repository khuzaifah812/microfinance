# Endpoints

SFMS is a server-rendered application. It does **not** yet ship a token-based REST API for a mobile app. The endpoints below return JSON or accept machine calls; all except the health check and webhooks require a signed-in session (cookie + CSRF for POST) and the stated permission.

| Method & path | Auth / permission | Purpose |
|---|---|---|
| GET `/healthz` | none | `ok` (200) when the database responds, else 503. Use for uptime monitoring |
| GET `/loans/preview/?product=<id>&amount=<n>&term=<n>` | `loans.view` | JSON repayment preview: rate, method, total interest, total payable, first instalment, `rows[]` (no, due, principal, interest, total). Returns `{"error": "..."}` when the rate is not set or inputs are out of range |
| GET `/members/<id>/export/` | `members.export` | A member's personal data as a JSON download (data-subject access) |
| POST `/session/keepalive/` | signed in | Extends the idle timer (used by the "Stay signed in" button) |
| POST `/integrations/webhook/<provider>/` | HMAC signature | Provider callback. `<provider>` is `mtn_momo` or `airtel_money` |

## Webhook contract (current)
Header `X-Signature`: lowercase hex HMAC-SHA256 of the raw request body using `MTN_MOMO_WEBHOOK_SECRET` / `AIRTEL_MONEY_WEBHOOK_SECRET`. Body:
```json
{"status": "SUCCESSFUL", "external_ref": "provider-transaction-id", "phone": "0772000000", "amount": 50000, "reference": "MB000123"}
```
Unsigned or wrongly signed calls get 403 and are audited. Only SUCCESSFUL statuses create a record; a repeat of the same `external_ref` is ignored. The payload parser is a documented **TODO**: map it to the official MTN MoMo / Airtel Money callback schema for your merchant account before going live. The record starts as "unmatched" until staff match it to an account (Integrations > Match) unless the reference identifies a member.

## Toward a mobile API
Add Django REST framework (or plain JSON views) with token authentication in a new app, reuse the service functions in `savings/services.py` and `loans/services.py` (they hold all business rules, journals and audit), and expose read-only balances and schedules first. Do not duplicate business logic in views.

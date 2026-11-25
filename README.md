# BinaryPro – Binary Network Management System

Django web application for a product-sales-based binary network: two-leg genealogy, product shop, sales-volume commission engine, wallet ledger, withdrawals and an admin back office.

## Quick start (Windows)

```powershell
py -3.11 -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env            # edit SECRET_KEY / DATABASE_URL
python manage.py migrate
python manage.py seed_demo        # optional demo data (A–G tree, products, orders)
python manage.py runserver
```

Open http://127.0.0.1:8000. Demo logins (password `Demo@1234`): `admin`, `asha` (root), `bhavesh`, `chetan`, `deepa`, `esha`, `farhan`, `gauri`, customer `kiran`.

Without demo data, create the company admin with `python manage.py createsuperuser`, then register the first distributor (they become the tree root) from `/accounts/register/`.

Run tests: `python manage.py test apps`

## Database

SQLite is used when `DATABASE_URL` is empty. For PostgreSQL (recommended for production; row locks only take effect there):

```
DATABASE_URL=postgres://user:password@localhost:5432/binarypro
```

## Modules

| App | Responsibility |
|---|---|
| `accounts` | Custom `User` (member ID, mobile, role), login by username or member ID, registration, password reset |
| `members` | `Referral` (sponsor, separate from placement), `MemberProfile` (address, PAN, bank/UPI, KYC) |
| `binary_network` | `BinaryNode` (parent + left/right, materialized path), `BinaryVolume` (leg counts, carry, totals), `BinaryVolumeEntry` (per-order volume log), placement + spillover, tree API |
| `products` | Categories, products with price + business volume (BV), stock |
| `orders` | Session cart, `Order`/`OrderItem` (price snapshots), `PaymentTransaction`, payment confirmation |
| `commissions` | `CommissionRule` (configurable rates/caps), `Commission` ledger, **engine** |
| `wallet` | Immutable `WalletTransaction` ledger; balance is always computed |
| `withdrawals` | Requests with payout snapshot; held on request, refunded on rejection |
| `notifications` | In-app alerts (+ optional email) |
| `reports` | Member dashboard, admin dashboard, members/orders/commissions/withdrawals/reports pages |
| `core` | `AuditLog`, helpers, `seed_demo` command |

## Commission engine (`apps/commissions/engine.py`)

Runs **only after an order is paid**. Nothing is paid for registration or joining.

1. Lock the order; skip if already processed (idempotent).
2. Direct-sale commission: `direct_sale_rate`% of order BV to the buyer's sponsor.
3. Walk up the tree from the buyer (a customer's volume enters at their sponsor's node) and add the BV to each ancestor's left or right carry.
4. Match: `matched = min(left_carry, right_carry)`; pay `matched × binary_match_rate`%, limited by `daily_binary_cap`; subtract the matched volume from both carries.
5. Commissions are created as **pending**. The admin releases them into wallets (Admin → Commissions).
6. Refunds reverse the order's commissions (released ones are clawed back from the wallet) and remove its unmatched volume.

Safeguards: `transaction.atomic`, `select_for_update` row locks, unique idempotency keys on commissions and wallet entries, unique (order, member) volume entries, and an audit log for every money action.

## Plan settings (the three business decisions)

These are set in Django admin → **Commissions → Plan settings**, so the client's final decision needs no code change. Every change is written to the audit log.

| Decision | Setting | Options |
|---|---|---|
| 1. Placement | `placement_mode` | **Manual** (member picks a leg; spillover to the bottom of that leg; default) · **Automatic** (next free position, breadth-first, left before right) · **Admin** (distributors wait in Admin → Placements) |
| 2. Commission type | `binary_match_rate`, `direct_sale_rate` | Both (default) · binary only (direct = 0) · direct only (binary = 0) |
| 3. What is sold | Product `product_type` | Physical (stock and shipping) · Digital/service (no stock limit) · or a mix |

Rates are placeholders. Set them to the figures in the client's approved compensation plan.

## Security

| Requirement | Implementation |
|---|---|
| Password hashing | Django PBKDF2 plus validators (length, common, numeric, similarity) |
| OTP | `apps/accounts/otp.py`. 6-digit code, only an HMAC hash is stored, 5-min expiry, 5 attempts then the code is burned, 60 s resend cooldown, 5 per hour. Required to verify the mobile number and to confirm every withdrawal. |
| Login brute force | 5 failures per username and IP, then a 15-minute lockout (cache-based; use Redis in production) |
| CSRF / HTTPS | Django CSRF on all forms. With `DEBUG=False`: SSL redirect, HSTS, secure and HttpOnly cookies, `X-Frame-Options: DENY` |
| Role permissions | `admin_required` on all back-office views; members see only their own orders, wallet and downline |
| Payment verification | Razorpay checkout signature `HMAC(key_secret, order_id\|payment_id)` and webhook signature `HMAC(webhook_secret, body)`, compared in constant time. The webhook amount and currency are checked against the order. |
| Duplicate callbacks | `mark_paid` locks the order and does nothing unless it is still pending. `gateway_reference` is unique. |
| Encryption | PAN, bank account number, UPI ID and the withdrawal payout snapshot are encrypted with Fernet (`apps/core/fields.py`). Keys come from `FIELD_ENCRYPTION_KEYS` and support rotation. |
| Immutable ledgers | Wallet, commission, volume and audit records are read-only in the admin. Corrections are reversal entries, never edits. |
| Audit trail | `AuditLog` for registrations, placements, payments, refunds, releases, withdrawals, plan changes and blocked members |
| Reconciliation | `python manage.py reconcile` checks every commission and withdrawal against the wallet ledger (exit code 1 on mismatch; schedule it daily) |
| Secrets | All keys come from env vars. The app **refuses to start** with `DEBUG=False` if `SECRET_KEY` or `FIELD_ENCRYPTION_KEYS` is missing, or if demo payments are enabled. |

## Payments (Razorpay)

1. Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` in `.env`.
2. In the Razorpay Dashboard → Webhooks, add `https://<domain>/orders/webhooks/razorpay/` with the events `payment.captured`, `order.paid` and `payment.failed`.
3. Set `PAYMENT_DEMO_MODE=False`. Demo payments are automatically disallowed when `DEBUG=False`.

The webhook is the source of truth. The browser callback is a fast path, and both are idempotent.

## Background jobs

Without `CELERY_BROKER_URL`, commission processing runs inline. With Redis:

```
CELERY_BROKER_URL=redis://localhost:6379/0
REDIS_URL=redis://localhost:6379/1
celery -A config worker -l info          # Windows dev: add --pool=solo
```

Commission processing is then queued after the payment commits (`transaction.on_commit`) and retried on failure. The engine is idempotent, so retries are safe.

## Tests

`python manage.py test apps` covers placement, spillover, auto and admin placement, commissions, caps, refunds, withdrawals, OTP (expiry, attempts, rate limit), login lockout, encryption at rest, Razorpay signatures, duplicate callbacks, access control, digital products and reconciliation.

**Concurrency:** the database's unique (parent, position) constraint plus row locks guarantee that two signups can never take the same slot. A system-chosen slot that loses the race is retried. `ParallelRegistrationTests` fires 8 simultaneous registrations, and it runs only on PostgreSQL (SQLite serialises connections). Run the suite against PostgreSQL before go-live:

```
$env:DATABASE_URL="postgres://user:pass@localhost:5432/binarypro"; python manage.py test apps
```

## Production checklist

- PostgreSQL, Redis, a Celery worker, Gunicorn + Nginx (set `X-Real-IP`), HTTPS
- `DEBUG=False`, `SECRET_KEY`, `FIELD_ENCRYPTION_KEYS` (back up the keys: losing them makes bank data unreadable), `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`
- An SMS provider class for `SMS_BACKEND` (MSG91, Twilio, etc.) and SMTP email
- `python manage.py collectstatic`, a daily `reconcile` cron, automated encrypted DB backups with tested restores, error monitoring (e.g. Sentry)
- Still to build: KYC document upload and approval, TDS/admin-charge deductions if the plan requires them

## Legal (India)

The Consumer Protection (Direct Selling) Rules, 2021 (amended 2023) prohibit pyramid and money-circulation schemes. Before go-live the client should:

- have a qualified Indian lawyer review the compensation plan
- pay only on genuine product sales (this software never pays for registration or joining)
- provide seller agreements, consumer disclosures, grievance handling and a refund process
- make no promise of guaranteed income, and hold the required registrations

The software supports a binary hierarchy. That alone does not make a compensation model lawful.

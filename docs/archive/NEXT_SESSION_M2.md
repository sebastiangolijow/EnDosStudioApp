# Next Session Briefing — StickerApp Backend

> Read this first. Then `CLAUDE.md`. Then `docs/spec.md`. That's enough context to start.

---

## 1. Where we are now

**Milestone 1 (bootstrap + Docker local dev) is done and pushed to `main`** at commit `3e27f32` on https://github.com/sebastiangolijow/EnDosStudioApp.

The repo at `/Users/cevichesmac/Desktop/yeko_studio/endosstudio_project/endossutdio_backend/` now contains a working Django 4.2 + DRF + Postgres 15 + Stripe skeleton, runnable in Docker:

- 4 apps: `core`, `users`, `orders`, `payments`
- Custom User model (UUID PK, email USERNAME_FIELD, role admin/shop_staff/customer) — **migrated**
- `apps/orders/` and `apps/payments/models.py` are **intentionally empty** (domain modeling deferred to this session)
- Stripe service scaffolding exists (`apps/payments/services/stripe_service.py`); webhook view returns 200 stub
- `tests/base.py` with factory methods + 3 passing smoke tests
- `make up`, `make test`, `make migrate`, `make shell` all wired
- Spec moved into `docs/spec.md`; old `SESSION_START.md` archived to `docs/archive/`

---

## 2. State of the working tree

Clean. All M1 work landed in commit `3e27f32`. `main` is up to date with `origin/main`.

Local-only files (gitignored, must exist for Docker to come up):
- `.env` — copied from `.env.example` at the end of M1. Fill in real Stripe test keys when starting M2 if you want to exercise webhooks; otherwise the skeleton boots fine without them.

---

## 3. What to do next (Milestone 2)

The plan is in `CLAUDE.md` § Status > Next, but here it is in execution order:

### Step 1 — Re-read the spec (10 min)

Open `docs/spec.md`. Note specifically:
- The order lifecycle: customer uploads → frontend produces masks → checkout → Stripe → "in production" → shipped → delivered.
- What files come with each order: original image, die-cut mask, relief mask. PNG/JPG; relief mask might be PNG or JSON depending on what the frontend sends.
- The frontend (Vue + OpenCV.js, separate codebase) does ALL image processing. Backend stores files; it doesn't manipulate pixels.

### Step 2 — Design `Order` + `OrderFile` (with the user, not solo)

Don't write code first. Sketch the models in conversation:

- **`Order`**: status (`draft → placed → paid → in_production → shipped → delivered → cancelled`), customer FK, total amount, currency, shipping address fields, timestamps. Inherits `apps.core.models.BaseModel` (UUID PK + timestamps + created_by).
- **`OrderFile`**: FK to Order, a `kind` enum (`original`, `die_cut_mask`, `relief_mask`), a `FileField`, mime_type, size_bytes. Inherits `BaseModel`.
- Open question worth surfacing: do we need a separate `ReliefMask` model if the relief data is JSON instead of PNG? Probably no — store both as `OrderFile` with `kind=relief_mask` and a `content_type` field. Confirm with the user.
- Open question: how do we handle a draft order that gets abandoned? Probably TTL + a management command to clean up. NOT a day-1 concern.

Once aligned, write the models. Run `make makemigrations && make migrate`.

### Step 3 — Load-bearing auth integration test (the M2 gate)

Before touching Order/Stripe flows, confirm the auth foundation works end-to-end. Write `tests/test_auth_roundtrip.py`:

```python
class AuthRoundtripTests(BaseTestCase):
    def test_register_setpassword_login_me(self):
        # 1. POST /api/v1/auth/register/ with email + password
        # 2. Pull user from DB, get verification_token
        # 3. POST /api/v1/users/set-password/ with email + token + new password
        # 4. POST /api/v1/auth/login/ with email + new password → assert 200, has access token
        # 5. GET /api/v1/users/me/ with Bearer token → assert 200, email matches
```

If this passes, the allauth `EmailAddress` trap is correctly handled and the JWT pipeline works. **This test is the gate before Order/Stripe work** — don't proceed until green.

### Step 4 — Stripe checkout flow

Wire `POST /api/v1/orders/{uuid}/checkout/`:
- Customer authenticated; order belongs to them; status is `placed`.
- Service function: `create_checkout(order) -> client_secret` calls `StripeService().create_payment_intent(amount_cents=..., currency="eur", order_uuid=str(order.pk))`.
- Returns `{"client_secret": "..."}` — frontend confirms via Stripe.js.

Then handle the webhook properly:
- `apps/payments/views.py` `StripeWebhookView` already validates signature.
- Add event routing: on `payment_intent.succeeded`, look up `Order` by metadata `order_uuid`, call `transition_order_to_paid(order, event)` in `apps/orders/services.py`.
- Persist a local `PaymentIntent` record (now's the time to add the model in `apps/payments/models.py`).

### Step 5 — Then admin views

Django admin first — register `Order`, `OrderFile`, `PaymentIntent`. Customize list filters by status. That's free and gets the shop owner moving immediately.

DRF admin endpoints for a future Vue UI come **after** the shop owner has used Django admin for at least a week. YAGNI until then.

---

## 4. Locked decisions (do not relitigate)

Same list as before — all from the original SESSION_START.md, still in force:

| Decision | Value |
|---|---|
| Backend | Python 3.11, Django 4.2 + DRF, Postgres 15 |
| Multi-tenancy | NONE (single print shop) |
| Async | NO Celery, NO Redis on day 1 — add when there's a real need |
| Payments | Stripe only, not gateway-agnostic |
| Auth stack | JWT (simplejwt) + dj-rest-auth + django-allauth |
| User PK | UUID, named `uuid`, always `.pk` not `.id` |
| Business logic | Lives in `apps/<app>/services.py`, not views/serializers |
| Image processing in backend | NONE — frontend does OpenCV.js |
| Frontend | Out of scope for this repo |
| Infra | Docker + docker-compose, two compose files, NO staging env |
| Language defaults | `LANGUAGE_CODE = "es"`, `TIME_ZONE = "Europe/Madrid"` |

If you find yourself wanting to revisit any of these, **flag it explicitly** to the user — don't silently change course.

---

## 5. Bootstrap deviations from the skill (already applied, just so you know)

These were forced by reality during M1; CLAUDE.md has them too:

1. `django-allauth` `>=0.57,<0.62` → **`>=65.0,<66.0`** (the modern `ACCOUNT_LOGIN_METHODS` / `ACCOUNT_SIGNUP_FIELDS` API only landed in 65.x; 0.61 still required the deprecated `ACCOUNT_AUTHENTICATION_METHOD` style).
2. `dj-rest-auth` `>=5.0,<6.0` → **`>=7.0,<8.0`** (allauth 65.x compat).
3. `REST_AUTH["TOKEN_MODEL"] = None` added to `config/settings/base.py` (dj-rest-auth 7.x defaults to legacy Token; we use JWT only).
4. `whitenoise` moved from `prod.txt` to **`base.txt`** (settings reference its middleware unconditionally; dev tests need it loadable).

---

## 6. Pitfalls already paid for

These are the same ones from the original SESSION_START — leaving them here so they don't get lost in the archive:

### The django-allauth `EmailAddress` row trap

allauth authenticates against `allauth.account.models.EmailAddress`, NOT against `User.email`. A user with correct password + `User.email` set but no matching `EmailAddress` row **silently fails to log in**. `User.verify_email()` and `tests/base.py:create_user` both handle this — any new flow that activates a user must too.

### `.pk` not `.id`

`User` and every domain model uses UUID PK named `uuid`. `user.id` raises `AttributeError`. Use `.pk` everywhere; `str(obj.pk)` in test assertions.

### `SIMPLE_JWT["USER_ID_FIELD"] = "uuid"`

Already set in `config/settings/base.py`. Don't change it — leaving it at default `"id"` makes JWT issuance silently fail.

### `restart` vs `up -d --force-recreate`

For env var changes in Docker, `restart` does NOT reload env vars. Use `down` + `up -d`, or `up -d --force-recreate`. Documented in CLAUDE.md too.

### Don't `rsync .env.production`

Not relevant until first deploy, but: the deploy script (when it exists) MUST NOT include `.env.production` in any rsync source list. Overwriting the server's env is the single biggest deploy footgun on LabControl.

---

## 7. Reference codebase

`/Users/cevichesmac/Desktop/labcontrol/` — YeKo's other Django backend. **Read for patterns. Do NOT import from. Do NOT depend on.**

When stuck on M2 questions, useful files:
- `apps/studies/models.py` — how a domain model with a status lifecycle is shaped
- `apps/payments/` — reference for invoice/payment record shape (LabControl is more elaborate; copy ideas, not weight)
- `apps/users/views.py` `SetPasswordView` — the exact pattern that handles the allauth trap
- `tests/base.py` — `BaseTestCase` factory patterns we mirror

What to ignore: `lab_client_id` multi-tenancy, Firebird sync code, healthcheck/deploy scripts (different infra).

---

## 8. Recommended reading order for the next session

If full token budget:
1. This file (`NEXT_SESSION.md`)
2. `CLAUDE.md`
3. `docs/spec.md`
4. `apps/users/models.py` + `apps/users/views.py` (skim — to understand what's already wired)
5. `apps/orders/models.py` + `apps/orders/services.py` (note how empty they are; that's M2)

If budget tight: this file only, then jump straight to step 1 of M2 ("re-read the spec").

---

## 9. M2 success criteria

You'll know M2 went well if, by the end:

- [ ] `Order` and `OrderFile` models exist + migrations applied
- [ ] Order admin is registered in Django admin
- [ ] `tests/test_auth_roundtrip.py` is written and green (registration → set-password → login → /me/)
- [ ] `POST /api/v1/orders/{uuid}/checkout/` returns a Stripe `client_secret`
- [ ] Stripe webhook routes `payment_intent.succeeded` → transitions order to `paid` (covered by an integration test)
- [ ] `PaymentIntent` model exists in `apps/payments/models.py` with at minimum: FK Order, stripe_payment_intent_id, status, amount, currency, raw_event JSONField
- [ ] CLAUDE.md updated to mark "M2 done" + add anything learned

---

## 10. If something goes wrong

- **Docker daemon not running** → `docker info` first; suggest the user start Docker Desktop.
- **Port 5432 in use** → ask the user to free it; do NOT silently change compose ports.
- **Migration looks wrong** → don't `--fake` past it. Read the diff, decide if the model is wrong or the migration is.
- **The user wants to deviate from a locked decision in §4** → don't silently comply. Surface the trade-off, let them confirm.
- **You disagree with this briefing** — CLAUDE.md wins. CLAUDE.md is the project's living doc; this file is a one-time briefing for the M2 startup.

---

*Created at the end of M1 (2026-05-01). Once M2 ships, this file gets either updated for M3 or archived to `docs/archive/NEXT_SESSION_M2.md`. Same convention as the previous one.*

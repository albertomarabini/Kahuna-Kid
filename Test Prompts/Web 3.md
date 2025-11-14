## The Problem

We’re building a **“BTCPay-like self-hosted payment gateway for sBTC + Stripe-style API for Bitcoin on Stacks”** — a simple payment gateway that lets merchants accept **Bitcoin (sBTC)** as easily as sending a link or using a qrcode.

Merchants will manage their own **store accounts** (with branding, webhook, and API key), invoices, and subscriptions — similar to how BTCPay organizes merchants and stores.

We must build a service interface that lets businesses easily accept Bitcoin payments via sBTC on Stacks. The goal is a seamless, low-friction payment gateway - think “Stripe for sBTC, with BTCPay-style checkout.”

* **Merchants**: Get paid in Bitcoin with zero technical setup.
* **Customers**: Pay with Bitcoin in one click, no hassle.
* **Stores**: Each merchant has a store profile that powers checkout branding and webhooks.

---

## Features

* **Simple Clarity contract** for accepting sBTC payments (`contracts/sbtc-payment.clar`).
  The `pay-invoice` function is called via wallet and transfers sBTC using the contract’s logic.
  ✅ Payments are **always contract-calls**, never raw fungible token transfers built off-chain.
  > **Required:** Every payable invoice **must be created on-chain** via `create-invoice`. Off-chain invoices are not payable. The backend must persist the 32-byte invoice ID (as 64-char hex) so wallets can call `pay-invoice` with the exact `(buff 32)` identifier.

* **Bridge to Clarity** Node.js with REST endpoints to generate invoices and build unsigned **contract-call payloads** (`pay-invoice`) using [`@stacks/transactions`](https://www.npmjs.com/package/@stacks/transactions).
* **Environment-based configuration** for network type, merchant (store) address, and sBTC token contract address.
* **Invoice + Store Database**
* **WEBAPP** Node.js +ejs server to serve the UI

Key features:

1. **Stores / Merchant Accounts** – each merchant has a store profile (API key, webhook secret, branding, support links).
2. **Magic Payment Links** – a merchant generates a link (invoice) and sends it to a customer. The customer clicks, opens their wallet, and pays in one step.
3. **Automatic Payment Detection** – no manual checking; the system automatically marks invoices as paid once confirmed.
4. **USD Pricing Snapshot** – checkout shows the USD value snapshot at invoice creation.
5. **BTCPay-style Checkout Page** – branded invoice screen with store logo/name, QR code, expiration countdown, and status badge.
6. **Merchant Dashboard** – manage invoices, refunds, subscriptions, webhook logs, and **store settings** (branding, keys, webhook).
7. **Refunds and Subscriptions** – merchants can issue refunds or set up recurring invoices.
8. **Admin Console (POC)** – single‑user admin UI to:
9 **POC scope:** one admin only (no RBAC). Auth via `ADMIN_TOKEN` env or basic auth.


# **what needs to be implemented**, with the core **features/methods/endpoints/queries** for each — structured for **clarity and future detailing**.

## 🔐 1. Clarity Smart Contract (on-chain)

The **Clarity smart contract** is a **program deployed on the Stacks blockchain** — specifically written in **Clarity**, a language designed for secure, deterministic smart contracts.

---

### ✅ Its Role in Your System

Think of it as your **"immutable payment rules engine"** that lives on-chain and **enforces core logic**, such as:

* Has this invoice been paid?
* Was the exact amount received?
* Did the payment arrive before expiration?
* Was it paid by a valid sender?

It acts as **the source of truth** for all sBTC payments.

---

### 🔗 How It Works

Smart contracts on Stacks are **invoked by transactions**.

For example:

1. The **payer’s wallet** signs and sends a transaction that **calls** `pay-invoice(id)` on the smart contract.
2. The smart contract then:

   * Checks the invoice exists.
   * Validates the amount.
   * Rejects if invoice is expired (`block-height >= expires-at`).
   * Transfers sBTC using SIP-010 `transfer?` (exact amount), then writes invoice state and emits an event.
   * Emits an event (that your backend can detect).

3. That's it — the contract just **executes and exits**.

**Additional Responsibilities for BTCPay-style behavior:**

Additional responsibilities for BTCPay-style behavior:

* Maintain a **merchant (store) registry**:
  * `register-merchant(principal, name?)` (admin-only).
  * `set-merchant-active(principal, active)` (admin-only).
* Enforce:
  * Only registered & active merchants can `create-invoice`.
  * Invoice’s `merchant` must still be active to accept payment.

---

### 🧠 Why It's Critical

The Clarity contract ensures that:

* The payment **logic is secure** and can’t be bypassed.
* The invoice **can’t be double-paid** or underpaid.
* Refunds only happen under defined rules.
* Anyone (e.g., your backend or a 3rd-party indexer) can **verify** payments directly on-chain.

---

### 🛠️ How Is being used by the Bridge

While the contract runs on-chain:

* Your **backend reads from it** (via the Stacks API).
* Your backend **does not modify it**, only **users’ wallets** do by submitting contract calls.

> Think of the Clarity contract as your **Stripe Terminal** — it's the "machine" the user pays into, but it lives on the blockchain.
> Your **backend is like the POS system** that watches the terminal and updates records in response.

**Artifact Name**: `sbtc-payment`

**Purpose**: On-chain invoice tracking, sBTC payments, and refunds.

**To Implement**:
* **Functions**:
   * `bootstrap-admin()` → 🔒 [One-time; sets the initial admin to `tx-sender`. Fails if already set]
   * `set-sbtc-token(contract-principal)` → 🔒 [Admin-only; **typed** as `(contract-of ft-trait)` to enforce compile-time trait match]
   * `register-merchant(principal, name?)` → 🔒 [Admin-only; adds merchant to registry as active]
   * `set-merchant-active(principal, active)` → 🔒 [Admin-only; toggles merchant active flag]
   * `create-invoice(id, amount, memo, expires-at?)` → 📌 [caller is the `merchant`; requires **registered & active** merchant; rejects duplicate `id`]
   * `pay-invoice(id)` → 📌 [rejects if paid, canceled, or expired (`block-height >= expires-at` when present); **enforces exact amount** via SIP-010 `transfer?`]
   * `refund-invoice(id, amount, memo?)` → 📌 [only invoice’s merchant; invoice must be paid; **cumulative cap**: `refund-amount + amount <= original amount`; tracks `refund-amount`]
   * `create-subscription(id, merchant, subscriber, amount, interval)` → 📌 [caller must equal `merchant`; merchant must be active; `interval > 0` blocks; subscriber must be a principal]
   * `pay-subscription(id)` → 📌 [caller must be subscriber; **`block-height >= next-due`**; transfers exact `amount`; schedules next period]
   * `cancel-invoice(id)` → 📌 [sets `canceled = true`; callable by merchant **or** admin; invoice must not be paid]
   * `cancel-subscription(id)` → 📌 [sets subscription `active = false`; callable by subscription merchant **or** admin]
   * `get-invoice-status(id)` → 📌 returns `"paid"`, `"unpaid"`, `"canceled"`, `"expired"`, or `"not-found"`
   * `mark-expired(id)` → 📌 callable event helper; prints `invoice-expired` if `block-height >= expires-at`

> ⚠️ `set-sbtc-token` is typed as `(contract-of ft-trait)` in Clarity, which enforces a **compile-time** trait match for the provided contract principal. The Admin UI should still warn that even trait-typed tokens can fail at runtime if the external contract misbehaves.



* **Read-Only Functions**:
   * `get-invoice(id)`
   * `is-paid(id)`
   * `get-invoice-status(id)` → 📌 returns `"paid"`, `"unpaid"`, `"canceled"`, `"expired"`, or `"not-found"`
   * `get-subscription(id)`
   * `next-due(id)`
   * `get-sbtc()` → 📌 returns **optional** current sBTC token contract principal
   * `get-admin()` → 📌 returns **optional** admin principal

* **Emits Events** for:
  * `invoice-created`
  * `invoice-paid`
  * `invoice-refunded`
  * `invoice-canceled` → 📌 [emitted when `cancel-invoice` is called]
  * `invoice-expired` — optional, via a cheap callable `mark-expired(id)` that prints when `block-height >= expires-at`; backend may also detect off-chain without calling.
  * `subscription-created`
  * `subscription-paid`
  * `subscription-canceled`

* **Enforce**:
  * Merchant registry:
    * Only **registered & active** merchants can `create-invoice`
    * Invoice’s `merchant` must still be active to accept payment
  * Invoices:
    * Exact payment amount — contract rejects under/over-payments
    * Expiration — `pay-invoice` rejects if `block-height >= expires-at` (when present)
    * Uniqueness — `create-invoice` rejects duplicate `id`
    * Double-pay prevention — `paid = true` blocks replay
    * Canceled invoices cannot be paid
    * `cancel-invoice` — merchant **or** admin only; only when not paid
    * Refunds — only invoice’s merchant can call; cumulative cap `refund-amount + amount ≤ original amount`
  * Subscriptions:
    * Creation — caller must equal `merchant`; merchant must be active; `interval > 0`
    * Payment — caller must be `subscriber` and `block-height ≥ next-due`
    * Cancel — `cancel-subscription` by subscription merchant **or** admin
  * Admin authority — `bootstrap-admin`, `set-sbtc-token`, and merchant registry setters are admin-only
    * POC admin is **immutable** after `bootstrap-admin`. To change the admin, redeploy a new contract and reconfigure the backend.

**Admin operations used by the Console:**
- `register-merchant(principal, name?)` (admin-only)
- `set-merchant-active(principal, active)` (admin-only)
- `set-sbtc-token(contract-principal)` (admin-only)

> Admin UI will surface these as “Sync Store on‑chain”, “Activate/Deactivate Store”, and “Set sBTC Token”.

---

## 🧠 2. Bridge (Node.js / Express)

**Artifact Name**: `payment-backend`

**Purpose**: Business logic, coordination, API endpoints, payment tracking.

---

awesome — you’re super close. below is a **clean, copy‑paste MD fix** that:

* moves `public-profile` to **Customer-facing** (so RN/Web can fetch it without an API key),
* fixes the **broken JSON fence**,
* adds a **private profile** endpoint for the dashboard,
* **dedupes** the internal logic bullets,
* clarifies **CORS** and **camelCase vs snake\_case**,
* and tightens the DB bits.

---

### 🌍 Customer‑facing Endpoints (public, global)

* `GET /i/:invoiceId` → serves the **public invoice DTO** (includes store branding/profile).
* `POST /create-tx` → builds **unsigned `pay-invoice`** with **two fungible post-conditions** (payer “sent ≥”, merchant “received ≥” sBTC).
* `GET /api/v1/stores/:storeId/public-profile` → public store fields (logo, displayName, brandColor, support links).
* `POST /api/v1/stores/:storeId/prepare-invoice` to obtain **{ invoice, magicLink, unsignedCall, WEBPAYURI }**

**Normative UI rule:** The checkout page **MUST** trigger the wallet *immediately* using either:

* the **WEBPAY deeplink** (`WEBPAYURI`) if provided, or
* the **unsignedCall** from `/create-tx` (Connect) as a fallback.
  Preflight/CORS and 64-hex `id_hex` validation remain required on these routes.&#x20;

  **Notes**
* CORS allowed only for origins in `merchants.allowed_origins`.
* Response uses **camelCase** field names; DB keeps snake_case.
* `/create-tx` MUST only build payloads for invoices that are: **found**, **unpaid**, **not canceled**, **not expired**, and whose **merchant is active**.
* `/create-tx` MUST attach **two fungible post-conditions** to enforce directionality:
  * **Payer**: “sent ≥ amountSats” for the payer principal (sBTC asset).
  * **Merchant**: “received ≥ amountSats” for the merchant principal (sBTC asset).
* Validate the invoice ID before building payloads:
  * `id_hex` is **exactly 64 hex chars** and **round-trips** to a `(buff 32)`.
* Public routes must implement **rate-limiting** and handle `OPTIONS` preflight. Include `Access-Control-Allow-Headers` for the API key and **both** webhook HMAC headers.
* `POST /api/v1/stores/:storeId/prepare-invoice` is called to obtain **{ invoice, magicLink, unsignedCall, WEBPAYURI }**
in one hop, while `/i/:invoiceId` and `/create-tx` continue to serve the page-first flow and tests.

####  WEBPAY mapping (wallet-first QR)
Build `WEBPAYURI` as a WEBPAY URL:
- **Scheme/encoding:** `web+stx:stxpay1…` (Bech32m HRP `stxpay`, ≤512 chars).
- **Operation:** contract-call to `pay-invoice` (encode address/name + function).
- **Fields:**
  - `recipient` ← `merchant_principal`
  - `token` ← sBTC SIP-010 contract (`contractAddress.contractName`)
  - `amount` ← `amount_sats` (base units)
  - `description` ← `memo` (optional)
  - `expiresAt` ← `quote_expires_at` (ISO-8601)
**UI usage:**
* **E-commerce servers** SHOULD call this once at “Place order” and **immediately render** the QR using `WEBPAYURI`; keep `magicLink` as universal fallback.
* **POS flows** SHOULD call this once when the amount is finalized and **display the QR** (wallet opens on scan).
* Checkout pages **SHOULD NOT** fetch additional data before prompting the wallet; branding/details can update in the background.&#x20;
* *The QR shown to customers **MUST encode** the `WEBPAYURI` when available; the visible “Pay” button **MUST** link to the same deeplink. Fallback is the `magicLink` page.&#x20;
---

### 🏬 Exposed Merchant API Endpoints (Used by any activity related to the merchant)
> **Subscription Mode (canonical):** Generate **per-period invoices** and subscribers pay via `pay-invoice`.
> **Advanced (optional direct):** `pay-subscription(id)` lets subscribers pay on schedule **without** creating an invoice.
> **UI requirement:** The dashboard must **clearly label** the mode per subscription, and the poller must handle both `subscription-paid` events and invoice lifecycles.

* `POST /api/v1/stores/:storeId/invoices`

* **POST /api/v1/stores/:storeId/prepare-invoice** → **single call** that:
  - creates the invoice (DB row) and returns the **public invoice DTO**,
  - builds the **unsigned `pay-invoice`** contract-call (same semantics as `/create-tx`),
  - returns a **WEBPAY deeplink** for wallet-first QR.

  **Request**
  ```json
  { "amount_sats": 25000, "ttl_seconds": 900, "memo": "Order #123", "webhook_url": null, "payerPrincipal": "SP..." }
```

**Response**

```json
{
  "invoice": { /* PublicInvoiceDTO */ },
  "magicLink": "/i/<invoiceId>",
  "unsignedCall": { /* unsigned pay-invoice with fungible PCs */ },
  "WEBPAYURI": "web+stx:stxpay1..."
}
```

**Rules**

* `unsignedCall` **MUST** include the same **two fungible post-conditions** as `/create-tx` (payer “sent ≥ amountSats” and merchant “received ≥ amountSats” for the sBTC SIP-010 asset).
* `WEBPAYURI` **MUST** use the **`web+stx:`** scheme with **Bech32m HRP `stxpay`** and **base-unit amounts**; it encodes a contract-call to `pay-invoice` (see *WEBPAY mapping* below).
* Does **not** remove or change public endpoints; it’s a convenience aggregator for POS/e-commerce servers.

* `GET /api/v1/stores/:storeId/invoices/:invoiceId`
* `GET /api/v1/stores/:storeId/invoices` → list invoices for this store
* `POST /api/v1/stores/:storeId/refunds`
* `POST /api/v1/stores/:storeId/subscriptions`
* `POST /api/v1/stores/:storeId/subscriptions/:id/invoice`
* `GET /api/v1/stores/:storeId/webhooks` → view webhook logs
* **`GET /api/v1/stores/:storeId/profile`** → full store profile (private fields; API key required)
* **`PATCH /api/v1/stores/:storeId/profile`** → update: `name`, `display_name`, `logo_url`, `brand_color`, `webhook_url`, `support_*`, `allowed_origins`
* **`POST /api/v1/stores/:storeId/rotate-keys`** → rotate `api_key` and/or `hmac_secret` (returns new values once)
* `POST /api/v1/stores/:storeId/subscriptions/:id/mode` → `{ mode: "invoice" | "direct" }`
- `POST /api/admin/invoices/:invoiceId/cancel` → admin override to cancel unpaid (calls on-chain `cancel-invoice`); merchants can also cancel via their own endpoint.

* **Cancel unpaid invoice (merchant)**
  * `POST /api/v1/stores/:storeId/invoices/:invoiceId/cancel`
  * Marks invoice canceled in DB and calls on-chain `cancel-invoice` (merchant must own the invoice; invoice must be unpaid)

* **Cancel subscription (merchant)**
  * `POST /api/v1/stores/:storeId/subscriptions/:id/cancel`
  * Calls on-chain `cancel-subscription` (merchant or admin)

---

### 🔑 Exposed Admin API (used by the UI Admin Console)

**Auth**
- Env var: `ADMIN_TOKEN` (bearer token), or **Basic Auth** with `ADMIN_USER` / `ADMIN_PASS`.
- All admin endpoints require this auth.
- This is **POC‑only**, not multi‑tenant admin.

**Admin UI Routes**
- `GET /admin` → serves Admin Console SPA
- Static assets: `/admin/*`

**Admin API Endpoints**
- **Stores**
  - `POST /api/admin/stores` → create store (id, principal, display/brand fields)
  - `PATCH /api/admin/stores/:storeId/activate` → `{ active: true|false }`
  - `POST /api/admin/stores/:storeId/rotate-keys` → returns `{ apiKey, hmacSecret }` **once**
  - `POST /api/admin/stores/:storeId/sync-onchain` → calls/registers on chain (`register-merchant`, `set-merchant-active`)
  - `GET /api/admin/stores` → list all stores (with active status)
- **Chain Config**
  - `POST /api/admin/set-sbtc-token` → body: `{ contractAddress, contractName }` → calls `set-sbtc-token`
  - **Admin Key Control (wallet action)**
    - `Bootstrap Admin` (one-time) → calls on-chain `bootstrap-admin` from the operator wallet
> **Note:** In the demo, the admin is immutable after bootstrap. To change admin, redeploy and reconfigure.
> **Warning:** Even with trait typing, external token transfers can fail at runtime; show a red banner on failure and prompt operator to correct the token contract.

> **Warning:** The platform cannot runtime-verify full SIP-010 behavior. Even with trait typing, transfers can still fail at runtime; show a red banner on transfer failure and prompt operator to correct the token contract.

> **Warning:** The platform cannot runtime-verify SIP-010 conformance. Display a red banner if transfers fail after setting the token, and prompt the operator to revert or correct the token contract.
- **Poller**
  - `GET /api/admin/poller` → `{ running, lastRunAt, lastHeight, lastTxId, lagBlocks }`
  - `POST /api/admin/poller/restart` → restarts loop (POC: toggles in‑process poller)
- **Webhooks**
  - `GET /api/admin/webhooks?storeId=&status=failed|all` → latest logs
  - `POST /api/admin/webhooks/retry` → `{ webhookLogId }`
- **Invoices**
  - `GET /api/admin/invoices?status=unpaid|expired|…&storeId=` → filter
  - `POST /api/admin/invoices/:invoiceId/cancel` → marks canceled in DB and (optionally) calls on‑chain `cancel-invoice` if created there
- **Rotate Keys (response)**
`POST /api/admin/stores/:storeId/rotate-keys`
```json
{ "apiKey": "new_api_key_value", "hmacSecret": "new_hmac_value" }
```
- **Set sBTC Token**
`POST /api/admin/set-sbtc-token`
```json
{ "contractAddress": "SPXXXX...", "contractName": "sbtc-token" }
```
- **Poller Status**
`GET /api/admin/poller`
```json
{ "running": true, "lastRunAt": 1724300000, "lastHeight": 123456, "lastTxId": "0xabc...", "lastBlockHash": "0xdef...", "lagBlocks": 2 }
```
- **Cancel Invoice**
`POST /api/admin/invoices/:invoiceId/cancel`
```json
{ "canceled": true, "invoiceId": "..." }
```

> All write operations log an audit entry (optional POC: stdout or simple DB table).


**On‑chain parity:** when using the contract merchant registry, admin flows should call `register-merchant(principal, name?)` and `set-merchant-active(...)` accordingly.

---

### 🔧 Internal Logic

* Poll Stacks API for contract‑call events (`pay-invoice`, `refund-invoice`, `pay-subscription`).
* Also track: `invoice-canceled`, `subscription-created`, `subscription-paid`, and `subscription-canceled` events for dashboard parity and webhook dispatch.
* Trigger webhooks to merchant endpoints; sign with store‑scoped HMAC.
* Verify HMAC signatures (incoming/outgoing, as applicable).
* Build wallet‑ready transaction payloads (`pay-invoice`, refunds).
* USD pricing snapshot (via external API).
* Scheduler to generate recurring subscription invoices.
* Enforce `refund_amount ≤ invoice.amount_sats`; block duplicate refund attempts.
* Authenticate merchant actions with **per‑store API key**.
* Maintain webhook delivery logs and **durable retry** (attempt counter + backoff).
* Detect & mark expired invoices:
  - Translate quote TTL (seconds) to **block height** for on-chain `expires-at` at creation (`currentHeight + ceil(ttlSeconds / avgBlockSeconds)`).
  - Treat invoice as **expired** if either the DB quote is past **or** on-chain `get-invoice-status(id)` returns `"expired"`.
* Rate‑limit invoice creation per store/IP.
* Ensure invoice ID uniqueness and collision checks.
* Enforce invoice **ID immutability**: DB row `id_hex` must match on-chain `(buff 32)` exactly; reject mismatches.
* Before preparing a refund tx, **pre-check** merchant’s sBTC balance (read via token `get-balance` API) to avoid wallet failures.
* Reject any payload build where `id_hex` fails **64-hex** validation or cannot **round-trip** to a 32-byte buffer.

**CORS**

* Public endpoints (`/i/:invoiceId`, `/create-tx`, `/api/v1/stores/:storeId/public-profile`) enforce CORS using `merchants.allowed_origins`.
* Preflight (`OPTIONS`) MUST include:
  - `Access-Control-Allow-Origin` (validated against store’s allowed origins)
  - `Access-Control-Allow-Headers` including: `Content-Type`, your store API key header, and **webhook HMAC headers** (`X-Webhook-Timestamp`, `X-Webhook-Signature`).

---

## 🧾 3. Database (SQLite)

**Artifact Name**: `invoices.sqlite`

**Purpose**: Store merchant (store) profiles, invoice metadata, payment state, subscriptions, and webhook logs.

### Table: `merchants`

* `id` (UUID / storeId, PRIMARY KEY) → StoreID === MerchantID
* `principal` (string, NOT NULL, UNIQUE) → on‑chain merchant principal
* `name` (string, optional) → internal store name
* `display_name` (string, optional) → shown on checkout header
* `logo_url` (string, optional) → URL to store logo
* `brand_color` (string, optional) → hex color for checkout accent
* `webhook_url` (string)
* `hmac_secret` (string, NOT NULL)
* `api_key` (string, NOT NULL, UNIQUE) → used for store API auth
* `active` (bool, default 1)
* `support_email` (string, optional)
* `support_url` (string, optional)
* `allowed_origins` (text, optional) → CSV/domain list for CORS (web/RN)
* `created_at` (int, timestamp)

> Optional branding/support fields power the BTCPay‑like checkout (logo/name/color) and multi‑client usage (Web & React Native).

### Table: `invoices`

* `id_raw` (UUID, PRIMARY KEY)
* `id_hex` (64-char hex string; encodes 32-byte `(buff 32)` invoice ID)
* `store_id` (FK → merchants.id)
* `amount_sats` (integer)
* `usd_at_create` (real)
* `quote_expires_at` (int)
* `merchant_principal` (string) → denormalized mirror for quick lookup
* `status` (`unpaid` | `paid` | `partially_refunded` | `refunded` | `canceled` | `expired`)
* `payer` (string, principal)
* `txid` (string)
* `memo` (text)
* `webhook_url` (text, optional override; defaults to merchants.webhook\_url)
* `created_at` (int)
* `refunded_at` (int, nullable)
* `refund_amount` (integer, default 0)
* `refund_txid` (string, nullable)
* `subscription_id` (nullable, FK → subscriptions.id)
* `refund_count` (int, default 0)
* `expired` (bool, default 0)

* **Constraints**:
  * `CHECK (length(id_hex) = 64)`
  * `CHECK (id_hex GLOB '[0-9A-Fa-f]*')`

> `partially_refunded` is derived as `refund_amount > 0 AND refund_amount < amount_sats` with `status='paid'` until fully refunded.


### Table: `subscriptions`

* `id` (UUID, PRIMARY KEY)
* `store_id` (FK → merchants.id)
* `merchant_principal` (string, denormalized)
* `subscriber` (string, principal)
* `amount_sats` (integer)
* `interval_blocks` (integer) → measured in **Stacks block height intervals**
* `active` (bool)
* `created_at` (int)
* `last_billed_at` (int, nullable)
* `next_invoice_at` (int)
* `last_paid_invoice_id` (UUID, nullable)

### Table: `webhook_logs`

* `id` (UUID, PRIMARY KEY)
* `store_id` (FK → merchants.id)
* `invoice_id` (UUID, nullable)
* `subscription_id` (UUID, nullable)
* `event_type` (`paid` | `refunded` | `subscription` | `invoice-expired`)
* `payload` (text, JSON)
* `status_code` (int)
* `success` (bool)
* `attempts` (int)
* `last_attempt_at` (int)

### Recommended Indexes

* `CREATE INDEX idx_invoices_store ON invoices(store_id);`
* `CREATE INDEX idx_invoices_status ON invoices(status);`
* `CREATE INDEX idx_subs_store_next ON subscriptions(store_id, next_invoice_at);`
* `CREATE INDEX idx_webhooks_store ON webhook_logs(store_id);`
* `CREATE UNIQUE INDEX ux_invoices_id_hex ON invoices(id_hex);`


### Queries

* `SELECT * FROM merchants WHERE api_key=? AND active=1` → auth store by API key.
* `SELECT * FROM invoices WHERE store_id=?` → list invoices for a store.
* `SELECT * FROM subscriptions WHERE store_id=?` → list subscriptions for a store.
* `SELECT * FROM webhook_logs WHERE store_id=?` → list webhooks for a store.
* `UPDATE merchants SET active=0 WHERE id=?` → deactivate store.
* `UPDATE invoices SET status='paid', payer=?, txid=? WHERE id_raw=?` → mark paid.

---


## 🧩 4. Webhook Delivery Module

**Artifact Name**: `webhook-dispatcher.js`

**Purpose**: Notify merchant backend when invoices are paid.

**To Implement**:

* Fire `POST` request on payment:
  * Payload: { invoiceId, status, txId, payer, amountSats }

* Include HMAC signature headers:
  * `X-Webhook-Timestamp: <unix>`
  * `X-Webhook-Signature: v1=<hex>` where `hex = HMAC_SHA256(hmac_secret, timestamp + "." + rawBody)`
  * Reject if clock skew > 300s; keep a 10-minute replay cache keyed by signature.
* Retries and error logging
  * Store delivery logs to `webhook_logs` table
  * Exponential backoff retry (3–5 attempts)
  * Fire delayed jobs via polling loop or job queue
* Merchant verifies signature before accepting
  * Fire `POST` request on refund:
    * Payload: { invoiceId, status: "refunded", refundTxId, refundAmount }
  * Fire `POST` request on subscription invoice creation:
    * Payload: { subscriptionId, invoiceId, amountSats, nextDue, subscriber }
* Emit `invoice-expired` webhook if invoice has not been paid within expiration window
* admin conveniences:
  - Support **manual retry** by `webhookLogId` (idempotent).
  - Expose latest N logs via `/api/admin/webhooks`.


---

## 🔄 5. Payment Poller

**Artifact Name**: `poller.js`

**Purpose**: Periodically query Stacks API to detect confirmed payments.

**To Implement**:

* Poll every X seconds (configurable; recommended ≥ Stacks block time).
  * Check for contract call events: `pay-invoice`, `refund-invoice`, `pay-subscription`, `cancel-invoice`, `create-subscription`, `cancel-subscription`
  * Detect and process `refund-invoice` and `pay-subscription` events
  * Adjust polling interval to match Stacks block time (~30s) to avoid hitting rate limits

* If event found:
  * Update DB: set status, txid, payer
  * Trigger webhook if needed
  * Update invoice with refund metadata if applicable

* Recognize and persist:
  * `invoice-canceled` → mark canceled, emit webhook if configured
  * `subscription-created` → upsert subscription row if needed
  * `subscription-paid` → update `last_paid_invoice_id` or last paid time as applicable
  * `subscription-canceled` → set subscription `active = 0`

* Detect unpaid invoices that exceed `quote_expires_at` or Clarity `expires-at`
  - Mark as `expired` in DB
  - Trigger optional `invoice-expired` webhook

- Track in memory (and optionally in DB) the fields:
  - `lastRunAt`, `lastHeight`, `lastTxId`, `lagBlocks`
- Expose via `/api/admin/poller`.
- Add `/api/admin/poller/restart` to re‑init the loop.

**Confirmations & Reorgs**
* Config: `{ minConfirmations: 2, reorgWindowBlocks: 6 }` (increase on mainnet).
* Maintain a cursor `{ lastHeight, lastTxId, lastBlockHash }`.
* On tip regressions **or parent mismatch** (compare the stored `lastBlockHash` to the API’s parent), rewind up to `reorgWindowBlocks` and reprocess.
* Only mark invoices **paid/refunded** after `minConfirmations`.

---

## 🌐 6. External Service Integrations

**To Use**:

* **Stacks API**: for:
  * Read-only contract state
  * Confirm tx status
  * Poll contract logs (events)
  * Subscription state tracking

* **Pricing API**: BTC/USD quotes (cached)
* **Stacks Wallet** (Browser Extension / Mobile):
  * Contract-call: `pay-invoice`
  * Post-conditions: ≥ sats required
  * Merchant uses wallet to sign `refund-invoice`
  * Merchant may use wallet to sign `pay-subscription` (if manual)

_Admin Console will:_
- call wallet to sign `set-sbtc-token` and merchant registry updates (or use a configured admin key for testnet only).
- display current token contract (read‑only via Stacks API).


---

### ✅ End-to-End Flow

```
                             +-------------------------------+
                             |     [ Merchant Dashboard ]     |
                             |        (React Web UI)          |
                             +-------------------------------+
                                          |
                           create invoice | manage refunds/subs
                                          v
+-------------------------------------------------------------------------+
|                        Backend Service (Node.js / Express)             |
|------------------------------------------------------------------------|
|  - API endpoints (invoice, refund, subscription)                       |
|  - Magic link generator                                                |
|  - Wallet tx builder (Stacks tx payloads)                              |
|  - USD price snapshot service                                          |
|  - HMAC signature verifier                                             |
|  - Auth middleware (per-merchant API keys or JWT)                      |
|------------------------------------------------------------------------|
|                          Internal Modules                              |
|  +----------------     +----------------------     +----------------   |
|  |   Scheduler     |    |   Webhook Dispatcher |    |   Poller       |  |
|  | (cron-based)    |    |  (retry logic, logs) |    | (Stacks events)|  |
|  +----------------     +----------------------     +----------------   |
|       | generate invoices   | enqueue webhooks   | scan for         |  |
|       v                     v                    v                  |  |
|                    +------------------------                         |  |
|                    |    Webhook Queue       |                        |  |
|                    |  (retry/backoff logic) |                        |  |
|                    +------------------------                         |  |
+-------------------------------------------------------------------------+
        |                        |                        |
        v                        v                        v
+---------------      +-------------------      +----------------------+
|   Database     |     |     Stacks API    |     |  Stacks Blockchain   |
| (SQLite)       |     | (contract logs,   |     |  (Clarity contracts) |
|---------------|     |  tx status, etc.) |     |----------------------|
| Tables:        |     +-------------------      | - sBTC Token (SIP-010)|
|  - invoices    |                                | - Payment Contract   |
|  - subscriptions                               | - Events:            |
|  - webhook_logs                                |   - invoice-paid     |
|  - merchants                                   |   - invoice-refunded |
|  - auth_keys                                   |   - subscription-paid|
+---------------                                 +----------------------+
        ^
        |
        | Store state updates (invoice status, refunds, subs, logs)
        v

                             +-------------------------------+
                             |  [ WEBAPP                ]     |
                             |         (HTML/JS UI)           |
                             +-------------------------------+
                                          ^
                              Clicks Magic Payment Link
                                          |
                                          v
                              +-----------------------+
                              | [ Stacks Wallet ]     |
                              | - pay-invoice         |
                              | - sign refund tx      |
                              +-----------------------+

```
---

## 🔄 Concrete Data Flow (step-by-step)

### 1. **Prepare invoice (single call)**
* Merchant/e-commerce server calls `POST /api/v1/stores/:storeId/prepare-invoice`
  → returns **{ invoice, magicLink, unsignedCall, WEBPAYURI }**.

* Merchant or backend generates `invoiceId` (UUID), computes `idHex` (32 bytes), and stores:

  * `amountSats` (e.g., `25000`)
  * `usdAtCreate` (BTC/USD snapshot)
  * `quoteExpiresAt = now   5min`
  * `status = 'unpaid'`
* If created manually: via dashboard/API.
* If from subscription: auto-generated by backend scheduler.
* Backend **must** call `create-invoice` on-chain (required) so `pay-invoice` can enforce amount/expiry and emit canonical events. Persist the 32-byte ID as 64-char hex (`id_hex`) in the DB.


### 2. **Show link/QR immediately**
* Render **WEBPAY QR** from `WEBPAYURI` (wallet-first).
* Keep **magicLink** `/i/<invoiceId>` as universal fallback (page opens wallet using `unsignedCall`).


* Backend returns: `https://pay.yourdomain.com/i/<invoiceId>`
* Page fetches invoice via `GET /api/invoices/:id`, displays:

  * Amount in sats
  * USD snapshot
  * Expiry countdown (based on quote TTL)
  * (Optional) Subscription badge or note if recurring

### 3. **Wallet Contract Call**
* Wallet opens to sign/broadcast `pay-invoice(id)` with fungible PCs (payer “sent ≥”, merchant “received ≥”).

* Page triggers `pay-invoice(id)` contract call via Stacks Wallet.
* Includes post-conditions to enforce minimum payment (`amountSats`).
* Wallet signs & broadcasts transaction.

### 4. **Detect Payment**

* Backend polls Stacks API or listens for contract events:

  * `invoice-paid`
  * (Later: `pay-subscription`)
* Once confirmed:

  * DB is updated:

    * `status = 'paid'`
    * `payer = <principal>`
    * `txId = <txid>`

### 5. **Notify Merchant**

* If `webhookUrl` is set:

  * Backend sends `POST` to merchant backend:

    ```json
    {
      "invoiceId": "...",
      "status": "paid",
      "txId": "...",
      "payer": "...",
      "amountSats": ...
    }
    ```
  * Includes **HMAC signature** in headers for verification

### 6. **Refunds**

* Merchant clicks "Refund" on dashboard.
* Backend prepares transaction for `refund-invoice(id, amount)`.
* Wallet opens, merchant signs refund.
* On-chain event confirms refund.
* DB updated: `status = 'refunded'`, `refundTxId`, `refundAmount`, `refundedAt`.
* Webhook fires to merchant backend with refund details.

### 7. **Subscriptions**

* Merchant creates subscription via dashboard (`POST /subscription`).
* Backend stores:

  * `subscriptionId`, `merchant`, `subscriber`, `amount`, `interval`, `next_invoice_at`
* On a timer (cron/scheduler), backend:

  * Checks due subscriptions
  * Auto-generates new invoices linked to `subscriptionId`
  * Fires webhook: `subscription-invoice-created`
  * Subscriber must later call `pay-subscription(id)` via wallet when due

---

## 🔐 Security Basics

* Never trust values in browser (like USD or invoice state).
* Backend enforces rules (quote expiry, exact sats, status).
* Webhooks must be HMAC signed and verified by recipient.
  * Sign using merchant-specific secrets (per-merchant HMAC keys) for scoping & rotation
  * Admin-only functions restricted to contract-owner (Clarity-level auth)
* **Settlement is in sats**. USD is for display only.
* Post-conditions in wallet transactions enforce **directionality** (payer sent ≥, merchant received ≥).
* Before refunds, **pre-check** merchant sBTC balance to avoid failing `transfer?` at runtime.
* Contract enforces **exact match** on-chain.
* Subscriptions cannot auto-debit; subscriber must sign `pay-subscription`.
* **Admin bootstrap (immutable in POC)**: Admin is set once via `bootstrap-admin`. No rotation function is exposed in POC; redeploy to change.
* **Single admin (immutable)**: Exactly one admin principal is set once via `bootstrap-admin` and cannot be changed. To change admin in the demo, redeploy a new contract and reconfigure the backend.
* **Token typing**: `set-sbtc-token` uses a trait-typed contract principal (compile-time enforced). Still treat the external token as untrusted and handle transfer failures defensively.
- Admin endpoints **not** exposed publicly in production; for POC behind VPN or restricted IPs.
- Key rotation responses are shown **once**; never logged in plain text.

---

# UI Webapp

WEBPAY is a **hosted checkout for sBTC** with three surfaces:

1. **Public**: magic‑link invoice pages, redirect checkout (`/checkout/:storeId`).
2. **Merchant Control Panel**: POS (`/pos/:storeId`), stores, invoices, subscriptions, branding.
3. **Admin Control Panel**: tenants, API keys, feature flags, audit.

WEBPAY is **server‑rendered (SSR)** with **tiny client islands**. All reads/writes to persistence and chain bridging are delegated to the existing **Bridge (Node “bridge to Clarity”)** via **server‑to‑server** calls.
*View Engine: EJS.*

**Interfacing applications** (already built):

| App                  | Runs        | Responsibilities                                                 | External              |
| -------------------- | ----------- | ---------------------------------------------------------------- | --------------------- |
| **Bridge**           | Node        | Persistence, unsigned call builders, `u` mint, webhooks, pollers | Stacks RPC/API        |
| **Wallet (Connect)** | User device | Signs/broadcasts                                                 | Stacks RPC via wallet |

---

## 2. Non‑Negotiable Architecture Rules

1. **Browser → WEBPAY only.** The browser never calls Bridge. Bridge endpoints are private (no CORS for WEBPAY pages). All browser polling/streams go to WEBPAY endpoints (e.g., `/status/:invoiceId`); only WEBPAY calls Bridge upstream. Bridge endpoints may expose CORS for SDK/diagnostics; WEBPAY pages never use them.
2. **WEBPAY server → Bridge** for **all** reads & writes (adds `X-API-Key`, verifies `u`).
3. **No hybrid classes.** Server files never touch DOM; client files never import Express or secret‑bearing services.
4. **Magic‑link pages**: WEBPAY verifies `u` **server‑side**, then the page runs **Stacks Connect** using data from `u`. **No `/create-tx` in the browser.**
5. **Status updates**: the UI polls `GET https://example.webpay.com/status/:invoiceId`. On each poll, WEBPAY calls Bridge `GET /i/:invoiceId` and returns a normalized DTO. No SSE in this POC. Bridge emits the invoice‑paid webhook (HMAC). WEBPAY runs no pollers.
6. **Secrets never leave WEBPAY** (API keys, HMAC secrets, admin creds).
7. **SSR first**: pages render from server templates; JS islands are minimal and page‑scoped.
8. **Output**: HTML pages via SSR + JSON for BFF endpoints (never raw Bridge passthrough).

---

## 3. Module Breakdown (Server‑Side)

### 3.1 Express App & Routing

* **Public routes**: `/`, `/checkout/:storeId`, `/invoice/:invoiceId`, `/status/:invoiceId` (JSON poll), `/w/:storeId/:invoiceId`.
* **Merchant routes** (auth required): `/merchant/*` — stores, invoices, subscriptions, branding, API key issuance.
* **POS** (merchant tool): `/pos/:storeId` (top‑level route, merchant context).
* **Admin routes** (admin auth): `/admin/*` — tenants, users, keys, feature flags, audit.

**Rule on URL ownership**
**Any route that starts with `https://example.webpay.com/…` is implemented by WEBPAY.**
**All other routes referenced here are Bridge‑hosted; WEBPAY interfaces with them server‑to‑server.**

#### 3.1.1 Public surface

* **GET `https://example.webpay.com/`** — Root landing.
* **POST `https://example.webpay.com/checkout/:storeId`** — Checkout Redirect API (merchant → WEBPAY; then WEBPAY redirects shopper to the magic‑link).
* **GET `https://example.webpay.com/invoice/:invoiceId`** — Invoice page (SSR preview/details).
* **GET `https://example.webpay.com/status/:invoiceId`** — JSON poll for status (UI → WEBPAY).
* **GET `https://example.webpay.com/w/:storeId/:invoiceId`** — Magic‑link page (public). WEBPAY verifies `u` server‑side; on success, the page opens the wallet via Connect. (Distinct from `/invoice/...`; `/w/...` is the wallet‑opening page.)

#### 3.1.2 Merchant Console

* **GET `https://example.webpay.com/pos/:storeId`** — POS application entry (merchant tool).
* **GET `https://example.webpay.com/merchant/:storeId`** — Merchant Control Panel entry.
* **`https://example.webpay.com/merchant/*` (auth)** — Merchant SSR routes (stores, invoices, subscriptions, branding, keys).
  *(Paths are grouped as `/merchant/*`; specific subpaths aren’t enumerated.)*

#### 3.1.3 Admin Console

* **GET `https://example.webpay.com/admin`** — Admin Control Panel entry.
* **`https://example.webpay.com/admin/*` (admin auth)** — Admin SSR routes (tenants, users, keys, feature flags, audit).

#### 3.1.4 Subscriptions (public links)

* **GET `https://example.webpay.com/s/:storeId/:subscriptionId?u=…`** — Subscription Pay Link (direct mode).
* **GET `https://example.webpay.com/s/:storeId/:subscriptionId/manage?u=…`** — *Intended* merchant‑only cancel/manage. **POC note:** no auth yet; the route may be publicly reachable but is only surfaced inside the Merchant Console (UI‑gated). When auth lands, restrict to authenticated merchants.

### 3.2 View Layer (SSR)

* **Templates**: EJS (with `ejs-mate` layouts), Tailwind CSS.
* Delivers fully rendered HTML with minimal data blobs for islands (e.g., `window.__PAGE__ = { invoiceId, magicLink, returnUrl, connectConfig }`).

### 3.3 Domain Services (BFF)

* `BridgeClient` (server‑only): signs requests, sets `X-API-Key`, maps errors to friendly messages.
* `MagicLinkService`: parse & verify `u` (HMAC, TTL, store binding); resolve invoice context. Sole owner of `u` verification. `BridgeClient` does not verify `u`.
* `InvoiceService`: create invoice, read by id, compute payable, status mapping, emit server events.
* `SubscriptionService`: CRUD subscriptions, plan lookup.
* `StoreService`: CRUD stores, POS config, redirect rules.
* `BrandingService`: fetch/cache theme/logo from Bridge.
* `AuthService`: sessions for Merchant/Admin; CSRF; rate limiting.

**Output:** typed DTOs for controllers & views; never raw Bridge payloads.

### 3.4 Client Islands

Small, page‑scoped TypeScript modules. **No secrets. No direct Bridge calls.**

* **Magic‑link opener** (`/w/:storeId/:invoiceId`): initializes Stacks Connect using **server‑rendered Connect config** (`connectConfig`); enforces `postConditionMode: 'deny'` and sBTC FT post‑condition; **polls `GET https://example.webpay.com/status/:invoiceId`** while `status === 'unpaid'`.
* **Status strip** (invoice page): polls **`GET https://example.webpay.com/status/:invoiceId`** (WEBPAY) on an interval and renders status.
* **POS screen**: renders QR of the **magic‑link** and a Cancel button; **polls `GET https://example.webpay.com/status/:invoiceId`** while unpaid.

---

## 4. Orientative Folder Structure

```
/webpay
  /src
    /server                # Server‑only Node code (never imported by client)
      app.ts               # Express init, middlewares, error boundaries
      /routes              # Route files (thin)
        public.ts
        merchant.ts
        admin.ts
      /controllers         # Request→service orchestration (no DOM)
        public/
        merchant/
        admin/
      /services            # Domain/BFF services (Bridge adapters, magic‑link verify)
        adapters/
          BridgeClient.ts
        MagicLinkService.ts
        InvoiceService.ts
        SubscriptionService.ts
        StoreService.ts
        BrandingService.ts
        AuthService.ts
      /middlewares
        csp.ts
        auth.ts
        errorHandler.ts
      /infra               # logging, config, http client, metrics
        config.ts
        logger.ts
        http.ts
        metrics.ts
      /views               # EJS templates + partials (canonical path)
        /_partials
          header.ejs
          footer.ejs
          status-strip.ejs
          toasts.ejs
        /layouts
          base.ejs
        /public
          landing.ejs
          magic-link.ejs
          invoice.ejs
          subscription-pay.ejs
        /merchant
          layout-merchant.ejs
          dashboard.ejs
          invoices.ejs
          invoice-detail-drawer.ejs
          subscriptions.ejs
          subscription-detail-drawer.ejs
          settings.ejs
          api-keys.ejs
          pos.ejs
          subscription-manage.ejs
        /admin
          layout-admin.ejs
          admin-home.ejs
          stores.ejs
          keys.ejs
          token-config.ejs
          poller.ejs
          webhooks.ejs
        error.ejs
    /client                # Browser code (tiny islands; page‑scoped)
      /islands
        magic-link.ts
        status-strip.ts
        pos.ts
      /styles
        tailwind.css
    /shared                # Isomorphic types/utilities (no secrets)
      /types
        dto.ts
      /utils
        base64url.ts
    /scripts               # CI lint/grep guards
      verify-no-bridge-in-client.mjs
      check-csp.e2e.mjs
  /public                  # Static assets served at /static/*
    /css
      app.css              # Tailwind build output
```

---

## 5. The *magic‑link*

A **magic‑link** is a WEBPAY‑hosted HTTPS URL that opens the wallet for a specific invoice:

```
https://example.webpay.com/w/<storeId>/<invoiceId>?u=...(&return)
```

* The **`u` blob is produced and HMAC‑signed by Bridge**, and **verified by WEBPAY** before rendering the page.
* The **link string is constructed by Bridge** (it knows WEBPAY’s base URL), then returned to WEBPAY and surfaced to the merchant/UI.

#### What it serves

A super‑small HTML page that immediately runs Stacks **Connect** and calls the wallet with a **contract call** for `pay-invoice`. If the browser blocks auto‑open, it shows a single **“Open wallet”** button.

#### Where it’s used

* **Web checkout redirect:** merchant → WEBPAY → magic‑link page opens wallet.
* **POS QR:** the QR shown on the terminal is **exactly** the magic‑link.
* **Emails/subscriptions:** email contains the **magic‑link**. Users click it; the wallet opens and they pay.

> No separate “checkout‑link”. The UI that previews amount/branding/expiry can be the same page if desired, but the link we surface externally is always the **magic‑link**.

---

### 5.1 Magic‑link page example

**This page is served only after WEBPAY verifies `u` server‑side.** Client checks are defense‑in‑depth.

The **WEBPAY checkout page** (opened by the magic‑link):

1. Shows amount, USD snapshot, TTL countdown, brand from `public-profile`.
2. Calls **Connect** with the embedded `unsignedCall` from `u`.
3. Displays a **QR of the same magic‑link** (for device handoff), not a raw wallet URI.
4. UI polls **`GET https://example.webpay.com/status/:invoiceId`** → WEBPAY polls Bridge `GET /i/:invoiceId`.
5. Shows a timer for expiration.

```html
<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>WEBPAY — Open your wallet</title>
<div id="ui" style="font:14px system-ui;padding:24px">Opening your wallet…</div>
<script type="module">
  import { request } from 'https://cdn.skypack.dev/@stacks/connect';

  const q = new URL(location.href).searchParams;
  const b64u = q.get('u') || '';
  const b64 = b64u.replace(/-/g, '+').replace(/_/g, '/');

  function hasFtPcDeny(uc) {
    const deny = uc.postConditionMode === 'deny';
    const ft = Array.isArray(uc.postConditions) &&
               uc.postConditions.some(pc => pc.type?.includes('ft') || pc.type === 'ft-postcondition');
    return deny && ft;
  }

  function parseU() {
    try {
      const data = JSON.parse(atob(b64));
      const now = Math.floor(Date.now()/1000);
      if (!data?.unsignedCall || !hasFtPcDeny(data.unsignedCall)) throw new Error('invalid-uc');
      if (typeof data.exp !== 'number' || data.exp < now) throw new Error('expired');
      return data;
    } catch {
      return null;
    }
  }

  const data = parseU();
  const ui = document.getElementById('ui');

  if (!data) {
    ui.textContent = 'This link is invalid or expired.'; // no wallet button
  } else {
    let opened = false;
    async function openWallet() {
      if (opened) return; opened = true;
      const uc = data.unsignedCall;
      const res = await request('stx_callContract', {
        contract: uc.contractId,
        functionName: uc.function,
        functionArgs: uc.args,
        postConditions: uc.postConditions,
        postConditionMode: 'deny',
        network: uc.network
      });
      const txid = res.txid || res.txId;
      ui.textContent = `Broadcasted: ${txid}`;
      const back = q.get('return');
      if (back) location.href = `${back}?txid=${encodeURIComponent(txid)}`;
    }

    openWallet().catch(() => {
      const b = document.createElement('button');
      b.textContent = 'Open wallet';
      Object.assign(b.style, { padding:'12px 16px', marginTop:'12px', borderRadius:'10px' });
      b.onclick = openWallet;
      ui.textContent = 'Ready to pay:';
      ui.appendChild(b);
    });
  }
</script>
```

**Before generating the checkout page**, WEBPAY must parse and validate `u`. If `u` is missing/invalid/expired → **render error UI and DO NOT open the wallet**.

1. **Require `u`** — if missing → **400** “Invalid link”.
2. **Decode + parse** — `u` is **base64url(JSON)**. Decode and `JSON.parse`. If parse fails → **400**.
3. **HMAC verification (anti‑tamper)** — JSON contains `sig` (HMAC‑SHA256). Compute:

   * `payload := JSON.stringify({ v, storeId, invoiceId, unsignedCall, exp })` (exact key order)
   * `mac := HMAC_SHA256(store.hmacSecret, payload)` (base64url)
   * Constant‑time compare with `sig`; mismatch → **403**.

* **Stability:** Use a stable/canonical stringify (or build the payload object in one literal) before HMAC to avoid key‑order drift.

  * *Provisioning:* WEBPAY holds the per‑store `hmacSecret` to verify `u`. Bridge also holds it to mint `u`. On rotate‑keys, both are updated.

4. **Path invariants** — `data.storeId === req.params.storeId` and `data.invoiceId === req.params.invoiceId`; else **400**.
5. **Expiry** — `nowSec := Math.floor(Date.now()/1000)`.

   * If `data.exp < nowSec` → **410 Gone** “Expired”.
   * If `data.exp - nowSec > 1800` → **400** “TTL too long”. *(TTL range is 120..1800s.)*
6. **Unsigned call shape (safety gate)**

   * `data.unsignedCall.function === "pay-invoice"`
   * `data.unsignedCall.postConditionMode === "deny"` (required)
   * `data.unsignedCall.postConditions` **includes at least one** FT post‑condition on the **payer** with **`willSendEq amountSats`** for the **sBTC** SIP‑010 asset id.
7. **Cross‑check against public DTO (fresh state)** — call Bridge `GET /i/:invoiceId` and verify:

   * `status === 'unpaid'` (reject `paid|expired|canceled` → **409**)
   * `amountSats` equals the FT PC amount
   * `quoteExpiresAt ≥ now` (defense‑in‑depth with step 5)
8. **Network** — `unsignedCall.network` must match deployment (`mainnet`/`testnet`) served by this WEBPAY instance; else **400**.

**On any validation failure, render `error.ejs` and set the corresponding HTTP status (400/403/409/410).**

**Only if all checks pass**, render the page and immediately open the wallet via Connect:

```ts
await request('stx_callContract', {
  contract: data.unsignedCall.contractId,
  functionName: data.unsignedCall.function,
  functionArgs: data.unsignedCall.args,
  postConditions: data.unsignedCall.postConditions,
  postConditionMode: 'deny',
  network: data.unsignedCall.network
});
```

**Client‑side guard (defense‑in‑depth)** — before calling Connect, the page re‑checks `u` locally (decode/parse/expiry; `postConditionMode === 'deny'`; presence of FT PC). If any check fails → show **invalid/expired** with **no** “Open wallet” button.

**`u` payload (signed JSON, base64url)**

```json
{
  "v": 1,
  "storeId": "store_abc",
  "invoiceId": "inv_8x3...",
  "unsignedCall": {
    "contractId": "SP...webpay",
    "function": "pay-invoice",
    "args": ["0x...invoiceId", "u25000"],
    "postConditions": [
      {
        "type": "ft-postcondition",
        "address": "<TX_SENDER or payerPrincipal>",
        "asset": "SP...sbtc-token::sbtc",
        "condition": "eq",
        "amount": "25000"
      }
    ],
    "postConditionMode": "deny",
    "network": "mainnet"
  },
  "exp": 1737324800,
  "sig": "<base64url HMAC-SHA256 over JSON.stringify({v,storeId,invoiceId,unsignedCall,exp})>"
}
```

---

### 5.2 Merchant → WEBPAY “Checkout Redirect API”

**Purpose:** a merchant starts a payment without holding WEBPAY/Bridge credentials.

**Route (public; no merchant secret in browser; caller = shopper’s browser):**

`POST /checkout/:storeId`

**Body (JSON or form‑encoded):**

```json
{
  "amount_sats": number,
  "ttl_seconds": number,         // 120..1800
  "memo": string,
  "orderId": string?,            // optional idempotency
  "payerPrincipal": string?,     // optional: if known by the merchant site
  "return": string?              // optional: absolute URL to return to after broadcast
}
```

**Response:** WEBPAY returns **302** to the shopper → `Location: magicLink`.

**WEBPAY server behavior:**

1. Validate basic shape (amount>0; TTL in [120..1800]).
2. **Server‑to‑server** call to Bridge: `POST /api/v1/stores/:storeId/prepare-invoice` with the stored per‑store `X-API-Key`.
3. Expect `{ invoice, magicLink, unsignedCall }` from Bridge.
4. **302 Redirect** shopper to `magicLink`.

> From the shopper’s perspective: Merchant → WEBPAY `/checkout/:storeId` → **redirect** to magic‑link → wallet opens.

```
POST /checkout/store_abc HTTP/1.1
Content-Type: application/json

{"amount_sats":25000,"ttl_seconds":900,"memo":"Order #123","payerPrincipal":"SP3...","return":"https://merchant.tld/thanks"}

HTTP/1.1 302 Found
Location: https://example.webpay.com/w/store_abc/inv_8x3...?u=...&return=https%3A%2F%2Fmerchant.tld%2Fthanks
```

---

## 6. Bridge API (called by WEBPAY)

> All endpoints below are **hosted by Bridge**. WEBPAY is their caller (with `X-API-Key` where required). Admin/Merchant consoles in WEBPAY are **just UIs** that drive these endpoints.

### 6.1 Key material provisioning (Bridge)

* **Rotate keys (admin/merchant)**

  * `POST /api/admin/stores/:storeId/rotate-keys` → `{ apiKey, hmacSecret }` (one‑time reveal).
  * `POST /api/v1/stores/:storeId/rotate-keys` (optional).
    WEBPAY stores both **apiKey** (to call Bridge) and **hmacSecret** (to **verify `u`** on magic‑link).

### 6.2 `POST /api/v1/stores/:storeId/prepare-invoice` (Bridge)

**Host:** Bridge. **Caller:** WEBPAY server (never the merchant browser). Returns `{ invoice, magicLink, unsignedCall }`. Bridge **mints `u`** and **builds the magic‑link** string.

*These pre‑creation gates are enforced in Bridge. WEBPAY and the magic‑link page only consume the payload.*

* `magicLink` is **constructed by Bridge**, pointing to WEBPAY `/w/...` with a freshly signed `u`.
* `unsignedCall` is what WEBPAY validates and what the page passes to Connect.

Validate inputs (amount>0; TTL ∈ [120..1800]). Bridge persists the invoice as unpaid with `quoteExpiresAt = now + ttl` and returns `{ invoice, magicLink, unsignedCall }`.

```json
{
  "invoice": {
    "invoiceId": "inv_8x3…",
    "storeId": "store_abc",
    "status": "unpaid",
    "amountSats": 25000,
    "usdAtCreate": "17.19",
    "quoteExpiresAt": "2025-09-18T12:34:56Z",
    "merchantPrincipal": "SPxxxxx",
    "memo": "Order #123"
  },
  "magicLink": "https://example.webpay.com/w/<storeId>/<invoiceId>",
  "unsignedCall": {
    "contractId": "SPxxxx.webpay",
    "function": "pay-invoice",
    "args": ["0x…invoiceId", "u25000"],
    "postConditions": [
      {
        "type": "ft-postcondition",
        "address": "<TX_SENDER or payerPrincipal>",
        "asset": "<SBTC_CONTRACT>::sbtc",
        "condition": "eq",
        "amount": "25000"
      }
    ],
    "postConditionMode": "deny",
    "network": "mainnet"
  }
}
```

> `magicLink` goes into **QR**, **redirects**, and **emails**. `unsignedCall` feeds `@stacks/connect`.

### 6.3 `GET /i/:invoiceId`

**Host:** Bridge. **Caller:** WEBPAY server only (no browser access, no CORS).

WEBPAY pages poll **`GET https://example.webpay.com/status/:invoiceId`**; WEBPAY then calls Bridge **`GET /i/:invoiceId`** upstream and returns a normalized DTO.

```json
{
  "invoiceId": "inv_8x3…",
  "idHex": "e3a1…9bc0",
  "storeId": "store_abc",
  "amountSats": 25000,
  "usdAtCreate": "17.19",
  "quoteExpiresAt": "2025-09-18T12:34:56Z",
  "merchantPrincipal": "SP3…",
  "status": "unpaid",
  "payer": "SP2…",
  "txId": "0xabc…",
  "memo": "Order #123",
  "subscriptionId": "sub_…",
  "createdAt": "2025-09-18T12:00:01Z",
  "refundAmount": 1000,
  "refundTxId": "0xdef…",
  "store": { "displayName": "Acme", "logoUrl": "https://…", "brandColor": "#FF7A00" }
}
```

---

## 7. Canonical flows

### 7.1 A) Merchant web checkout (redirect)

#### 7.1.1 Sequence

1. **Customer** clicks **Pay with sBTC** on the merchant site.
2. Shopper’s browser **POSTs to `https://example.webpay.com/checkout/:storeId`** (form or fetch; no secrets).

```
POST https://example.webpay.com/checkout/:storeId
Content-Type: application/json
{
  "amount_sats": 25000,
  "ttl_seconds": 900,
  "memo": "Order #123",
  "orderId": "123",
  "payerPrincipal": "SP..."
}
```

3. **WEBPAY server** calls Bridge (server‑to‑server): `POST /api/v1/stores/:storeId/prepare-invoice` (with stored `X-API-Key`).

**Bridge response**

```json
{
  "invoice": {
    "invoiceId": "inv_8x3...",
    "storeId": "store_abc",
    "status": "unpaid",
    "amountSats": 25000,
    "usdAtCreate": "17.19",
    "quoteExpiresAt": "2025-09-18T12:34:56Z",
    "merchantPrincipal": "SP...",
    "memo": "Order #123"
  },
  "magicLink": "https://example.webpay.com/w/store_abc/inv_8x3...?u=...&return=https%3A%2F%2Fmerchant.tld%2Fthanks",
  "unsignedCall": { /* same shape as in §5 — u payload */ }
}
```

4. WEBPAY responds **302** to the shopper → redirects to `magicLink`.
5. **WEBPAY magic‑link page** validates `u`, then calls `request('stx_callContract', …)` (wallet opens). On broadcast, show **Broadcasted** and, if `return=` was present in the magic‑link, redirect to it with `?txid=...`.
6. **UI** polls `GET https://example.webpay.com/status/:invoiceId` → WEBPAY calls Bridge `GET /i/:invoiceId` at every UI poll; Bridge emits the `invoice-paid` webhook (HMAC).

**Notes**

* **Fees/nonce** previews (for UX) can be computed using your node: `GET /v2/accounts/{principal}` and `POST /v2/fees/transaction`.

#### 7.1.2 Node.js calls consumed by this operation

##### 7.1.2.1 A1. Merchant creates an invoice

**WEBPAY action:** Merchant server hits WEBPAY to prepare an invoice, then 302‑redirects shopper to `magicLink`.

**Bridge interface:** `POST /api/v1/stores/:storeId/prepare-invoice`

**Request (from merchant → WEBPAY):**

* `amount_sats` (number) — from merchant order.
* `ttl_seconds` (120..1800) — from merchant config/UI.
* `memo` (string) — from merchant order.
* `orderId` (string, optional) — idempotency key.
* `payerPrincipal` (string, optional) — if merchant knows payer (locks FT PC principal).

**Response (WEBPAY → merchant) and usage:**

* `invoice` DTO → merchant may store.
* `magicLink` (e.g., `https://example.webpay.com/w/<storeId>/<invoiceId>?u=...&return=...`) → merchant **redirect target** and **QR content**.
* `unsignedCall` → exact spec the magic‑link page passes to Connect (serialized args + FT PC + `postConditionMode:'deny'`).

##### 7.1.2.2 A2. Shopper lands on the magic‑link page

**WEBPAY action:** the page itself opens the wallet via Connect with the embedded unsigned call.

**Bridge interface:** **None** (page uses `u=`; no fetch).

##### 7.1.2.3 A3. Status updates after broadcast

**WEBPAY action:** Any UI (page, POS, merchant) polls `GET https://example.webpay.com/status/:invoiceId`.

**Bridge interface:** `GET /i/:invoiceId`

**Response (WEBPAY → client):** DTO with `status`, `txId`, `payer`, branding `store` fields.

---

### 7.2 B) POS (terminal/app)

* Step 1: Clerk enters amount in **WEBPAY POS**; WEBPAY → Bridge `POST /api/v1/stores/:storeId/prepare-invoice` (short TTL).
* Step 2: Bridge returns `{ invoice, magicLink, unsignedCall }`.
* Step 3: WEBPAY renders **QR = magic‑link**. Customer scans → lands on WEBPAY magic‑link page → Connect opens with pre‑minted `u`.
* Step 4: POS polls WEBPAY `GET https://example.webpay.com/status/:invoiceId` until `paid`. (WEBPAY polls Bridge `GET /i/:invoiceId` upstream.)

> POS runs on **WEBPAY**. Only WEBPAY server uses the store `X-API-Key` to call Bridge; never expose the key to browsers or devices.

#### 7.2.1 POS architecture — detailed

**Actors**

* **Clerk on POS device** (your app)
* **WEBPAY** (your server)
* **Customer’s wallet** opened via **Connect**
* **Stacks infra** (Node RPC + Blockchain API) for reads/broadcast/confirmations

**Golden rule:** POS shows a **QR of the magic‑link** (`https://example.webpay.com/w/<storeId>/<invoiceId>?u=...&return=`). The wallet is only opened **from that page** via Connect; POS never shows a custom wallet URI.

#### 7.2.2 Happy path (sequence + data)

**App entry:** `GET https://example.webpay.com/pos/:storeId`

1. **Clerk enters amount** in WEBPAY‑hosted POS UI (merchant context) and submits.
2. **WEBPAY server → Bridge**: `POST /api/v1/stores/:storeId/prepare-invoice` (short TTL) using the store’s API key.

```http
POST /api/v1/stores/:storeId/prepare-invoice
Content-Type: application/json
X-API-Key: <merchant key>
{
  "amount_sats": 75000,
  "ttl_seconds": 300,
  "memo": "POS #7 sale",
  "orderId": "POS7-2025-09-19-001"
}
```

**Bridge response**

```json
{
  "invoice": {
    "invoiceId": "inv_8x3...",
    "storeId": "store_abc",
    "status": "unpaid",
    "amountSats": 75000,
    "usdAtCreate": "51.49",
    "quoteExpiresAt": "2025-09-19T11:22:33Z",
    "merchantPrincipal": "SP....",
    "memo": "POS #7 sale"
  },
  "magicLink": "https://example.webpay.com/w/<storeId>/<invoiceId>...",
  "unsignedCall": {
    "contractId": "SP....webpay",
    "function": "pay-invoice",
    "args": ["0x<invoiceIdHex>", "u75000"],
    "postConditions": [
      { "type": "ft-postcondition", "address": "<TX_SENDER>", "asset": "SP...sbtc-token::sbtc", "condition": "eq", "amount": "75000" }
    ],
    "postConditionMode": "deny",
    "network": "mainnet"
  }
}
```

3. **WEBPAY renders the QR = `magicLink`**. Customer scans → lands on the magic‑link page → wallet opens via Connect using the embedded `u`.
4. **Polling & completion:** POS polls `GET https://example.webpay.com/status/:invoiceId` until terminal status.

   * On `paid` → stop → show Paid ✓ (`txId`) → *New sale*.
   * On `expired` or `canceled` → stop → show Not paid → *Create new invoice* (prefill).
   * POS never relies on wallet callbacks; **status** is the source of truth.

#### 7.2.3 POS UI spec

* **New Sale**

  * Amount input (sats or fiat with preview)
  * TTL picker (default 2–5 min)
  * **Create** → calls prepare‑invoice; **renders magic‑link QR** + countdown + cancel button
  * Status strip: *Awaiting scan → Wallet opened (optional) → Broadcast seen → Paid ✓*
  * On expiry, offer **Create new invoice** (same amount)

* **End of transaction**

  * Paid → big Paid ✓, short `txId`, sound cue, *New sale* button
  * Not paid (Expired/Canceled) → status banner, *Create new invoice* (prefill)

* **Cancel option** (merchant)

  * Button near the QR: `POST /api/v1/stores/:id/invoices/:invoiceId/cancel/create-tx` (preferred on‑chain) or DTO fallback; after cancel, public builders for this invoice must refuse.

#### 7.2.4 Failure & edge cases

* **Expired before payment** — Magic‑link page shows **Expired**; CTA: “Ask clerk to regenerate”. POS flips to **Expired**; button: **New invoice**.
* **Customer never opened wallet / device issue** — POS continues polling; on TTL hit status becomes **Expired**. Magic‑link page keeps an **Open wallet** fallback.
* **Broadcast pending** — POS continues polling `/status/:invoiceId`; optionally show “Pending…” via Blockchain API.
* **Refunds / cancels (post‑sale)** — From the **Merchant dashboard**. POS may expose “Refund last sale” (unsigned `refund` → Connect).

> **Note:** Cancel is available in POS and Merchant Console only; the public magic‑link page does **not** expose a cancel action.

#### 7.2.5 Networking, auth, CORS

* POS is a merchant‑context client to WEBPAY (cookie/session/JWT when auth is added).
* API keys never appear in the browser. WEBPAY adds `X-API-Key` on server‑to‑server calls to Bridge.
* **All wallet opens happen on the magic‑link page via Connect** — never directly from POS.

#### 7.2.6 Why this is robust for POS

* **One QR type** (the magic‑link) keeps the terminal simple and multi‑wallet compatible.
* **Wallet UX** is delegated to Connect; FT post‑conditions + **Deny** mode provide safety.

#### 7.2.7 Minimal POS pseudo‑code

```ts
// create (browser → WEBPAY; WEBPAY calls Bridge with X-API-Key server-side)
const res = await fetch(`https://example.webpay.com/merchant/stores/${storeId}/prepare-invoice`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ amount_sats, ttl_seconds, memo })
});
const { invoice, magicLink } = await res.json();
showQR(magicLink);
startCountdown(invoice.quoteExpiresAt);

// poll (browser → WEBPAY; WEBPAY polls Bridge /i/:invoiceId upstream)
const poll = setInterval(async () => {
  const dto = await fetch(`https://example.webpay.com/status/${invoice.invoiceId}`).then(r => r.json());
  if (dto.status === 'paid') { clearInterval(poll); beepGreen(); showPaid(dto.txId); }
  if (dto.status === 'expired' || dto.status === 'canceled') { clearInterval(poll); showExpired(); }
}, 1200);

// cancel (operator) — preferred on-chain path with unsigned cancel from WEBPAY
async function cancelInvoice() {
  const r = await fetch(`https://example.webpay.com/merchant/stores/${storeId}/invoices/${invoice.invoiceId}/cancel/create-tx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  });

  if (r.ok) {
    const data = await r.json(); // { unsignedCall } or { canceled: true }
    if (data.unsignedCall) {
      const uc = data.unsignedCall;
      const res = await request('stx_callContract', {
        contract: uc.contractId,
        functionName: uc.function,
        functionArgs: uc.args,
        postConditions: uc.postConditions,
        postConditionMode: 'deny',
        network: uc.network
      });
      console.log('Cancel broadcasted:', res.txid || res.txId);
    } else if (data.canceled) {
      showExpired();
    }
  } else {
    const f = await fetch(`https://example.webpay.com/merchant/stores/${storeId}/invoices/${invoice.invoiceId}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    if (f.ok) showExpired();
  }
}
```

### 7.3 C) Subscriptions

#### 7.3.1 Subscription Pay page (direct mode)

**Route:** `GET /s/:storeId/:subscriptionId?u=…`
**Purpose:** Open wallet to execute `pay-subscription` when due.

`u` for subscription **pay** and **manage** links is **created and signed by Bridge**; links are **hosted by WEBPAY** under `/s/...` and validated like invoice links.

* **Collection mode:** **invoice‑mode**. Subscriptions generate periodic **invoices**; customers pay them via the **same magic‑link flow** (see §5, §6.2, §6.3).
* **Emailing:** send **magic‑link** on subscription invoice creation; no extra email management (POC scope).

#### 7.3.2 Merchant Console — Subscriptions

WEBPAY calls **Bridge** endpoints; the UI calls **WEBPAY** only. Responses include `magicLink` + `unsignedCall` minted by Bridge.

* **Index:** columns = Subscriber, Amount (sats), Interval (blocks), Status, Next due, Last billed, Mode (always `invoice`). Filters = Status, Next‑due window, text search.

* **Create Subscription (modal):** `POST /api/v1/stores/:storeId/subscriptions` → toast “Subscription created”.

* **Row actions:**

  * **Generate invoice now** → `POST /api/v1/stores/:storeId/subscriptions/:id/invoice` `{ ttl_seconds, memo? }` → success sheet with **Amount**, **Expires**, **Copy Link**, **Send Email**.
  * **Send email** → WEBPAY emailer with returned **magicLink**.
  * **Cancel subscription** → `POST /api/v1/stores/:storeId/subscriptions/:id/cancel` → may return unsigned cancel (sign via Connect); on success mark **Cancelled**.
  * **Details drawer** → schedule/history/linked invoices (with **Copy Link** and **Open invoice**).

* **Manage Link**: `GET /s/:storeId/:subscriptionId/manage?u=…` — intended merchant‑only; **POC:** UI‑gated, not linked publicly; enforce with auth later.

---

## 8. Admin Console

All **Admin endpoints** (`/api/admin/...`) are Bridge‑hosted. The WEBPAY Admin UI is a client. The **poller** and **webhook dispatcher** run in Bridge.
**Console entry:** `GET https://example.webpay.com/admin`

### 8.1 Shop Management Flows

**Create store (idempotent) + list**

* `POST /api/admin/stores` body: `{ principal, name, display_name, logo_url, brand_color, allowed_origins, webhook_url }`
* `GET /api/admin/stores` (on 409 to resolve idempotency)

**Activate / Deactivate store**

* `PATCH /api/admin/stores/:storeId/activate` body: `{ active: true|false }` → `{ active }`
* Effect: when **inactive**, builders (e.g., `/create-tx`) return 4xx; on‑chain `pay-invoice` aborts. Magic‑link may still open wallet; surface reason after confirm.

### 8.2 Admin Functions

**Admin bootstrap (on‑chain)**

1. `POST /api/admin/bootstrap` → `{ call }` (unsigned).
2. Broadcast via wallet (Connect). Treat `abort_by_response u1` as idempotent success.

**Rotate API/HMAC keys**

* Admin: `POST /api/admin/stores/:storeId/rotate-keys` → 1st call returns `{ apiKey, hmacSecret }`; subsequent calls do not re‑expose.
* Merchant (optional): `POST /api/v1/stores/:storeId/rotate-keys`.

> On rotate, WEBPAY stores both: `apiKey` (to call Bridge) and `hmacSecret` (to verify `u`). Bridge stores `hmacSecret` to mint `u`.

**Set sBTC token (required before builders work)**

* `POST /api/admin/set-sbtc-token` `{ contractAddress, contractName }` → `{ call }` (unsigned) → sign in wallet.

**On‑chain sync helper (admin)**

* `POST /api/admin/stores/:storeId/sync-onchain` → `{ calls: UnsignedCall[] }`

**Poller controls & admin logs**

* `GET /api/admin/poller` → `{ running, lastRunAt, lastHeight, lastTxId, lagBlocks }`
* `POST /api/admin/poller/restart` → `{ running }`
* `GET /api/admin/webhooks?status=all|failed&storeId=…`
* `POST /api/admin/webhooks/retry` `{ webhookLogId }`

---

## 9. Merchant Console

The WEBPAY Merchant UI calls WEBPAY endpoints; the WEBPAY server calls Bridge `/api/v1/...` using a server‑side session that carries the store’s **API key** (never exposed to browsers).
**Console entry:** `GET https://example.webpay.com/merchant/:storeId`

### 9.1 Private profile & branding (incl. CORS & webhook)

* `GET /api/v1/stores/:storeId/profile`
* `PATCH /api/v1/stores/:storeId/profile` `{ displayName, brandColor, allowedOrigins, webhookUrl }`
* Public preview: `GET /api/v1/stores/:storeId/public-profile`

### 9.2 Invoice Management

**Manual Invoice (dashboard DTO path)**

```
POST /api/v1/stores/:storeId/prepare-invoice
→ { invoice, magicLink, unsignedCall }
```

**Invoice Ledger — one table, state‑driven actions**

| Status     | Actions (row)                                          |
| ---------- | ------------------------------------------------------ |
| `unpaid`   | Copy Link · Open Checkout · **Cancel** · (POS) Show QR |
| `paid`     | **Refund** (drawer) · Print/Receipt · View Tx          |
| `expired`  | Create New (prefill) · Archive                         |
| `canceled` | —                                                      |

* **Cancel unpaid** — preferred: `POST /api/v1/stores/:storeId/invoices/:invoiceId/cancel/create-tx` → sign via Connect. Fallback: `POST /api/v1/stores/:storeId/invoices/:invoiceId/cancel` → `{ canceled: true }`.
* **Refunds** — `POST /api/v1/stores/:storeId/refunds/create-tx` `{ invoiceId, amount_sats, memo }` → unsigned `refund-invoice`.
* **Listings & details** — `GET /api/v1/stores/:storeId/invoices?status=…` · `GET /api/v1/stores/:storeId/invoices/:invoiceId`.

### 9.3 Refunds (partial / full)

Rules (contract‑enforced, surfaced in UI): must be paid; amount ≤ remaining; correct token (sBTC). On success, reflect `refundAmount`; webhook `invoice-refunded` may fire (HMAC).

### 9.4 Webhooks — merchant logs

* `GET /api/v1/stores/:storeId/webhooks`

---

## 10. Public Builder & Checkout (shared UI)

These endpoints are **Bridge‑hosted** for SDK/diagnostics. **WEBPAY magic‑link pages never call them.** WEBPAY starts checkout via **`POST https://example.webpay.com/checkout/:storeId`** and redirects to the magic‑link.

* **Bridge builder:** `POST /create-tx` `{ invoiceId, payerPrincipal }` → unsigned `pay-invoice`
  • Bad/unknown UUID → 4xx
  • **Expired/Cancelled/Inactive** → **409 invalid_state**
  • CORS preflight allowed (`OPTIONS /create-tx`)

* **Wallet & fees:** Wallet opens via Connect and returns `txId` upon broadcast. Fee/nonce estimates via Node RPC to improve totals UX.

> To start checkout via WEBPAY, merchants call **WEBPAY**: `POST https://example.webpay.com/checkout/:storeId` with `{ amount_sats, ttl_seconds, memo, orderId?, payerPrincipal? }`. WEBPAY calls Bridge `prepare-invoice` and redirects to the **magic‑link**.

---

## 11. Subscriptions (invoice‑mode + direct)

### 11.1 Subscriptions — links & UI

**Pay Link**
`https://example.webpay.com/s/<storeId>/<subscriptionId>?u=...`

* `u` is a short‑lived, signed blob with the prepared **`pay-subscription`** call (when due).
* Page: validate `u` → **Open wallet** via Connect → show result. If **not due**, show **Not due yet** and **next due** time.

**Manage Link (cancel)**
`https://example.webpay.com/s/<storeId>/<subscriptionId>/manage?u=...`
Exposed inside the Merchant Console; **POC:** publicly reachable but UI‑gated; requires merchant auth once implemented.

---

# Views & Templates Specs

### A.1 SSR boot (app.ts)

```ts
// app.ts
import express from 'express';
import path from 'node:path';
import ejsMate from 'ejs-mate';

app.engine('ejs', ejsMate);
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.use('/static', express.static(path.join(__dirname, '..', '..', 'public')));
```

### A.2 Branding Profile Injection (MUST)

Every rendered view **must** receive **branding** props sourced server‑side before render:

* For **public pages**: `GET /api/v1/stores/:storeId/public-profile` (WEBPAY → Bridge) → fields used: `displayName`, `logoUrl`, `brandColor`, `supportEmail`, `supportUrl`.
* For **merchant/admin** pages: `GET /api/v1/stores/:storeId/profile` (or admin aggregates) → at minimum `displayName`, `brandColor`; include `logoUrl` if available.

**Base layout** sets:

* CSS variables: `--brand: <brandColor || #111827>` (fallback slate‑900); derive shades as needed.
* Header: Logo (if `logoUrl`), else initial badge; Merchant name.
* Footer: Support link/email if provided.

> **Rule:** No page renders without a branding object; if fetch fails, the controller provides a safe default (neutral theme + generic name) and logs a warning.

> **Security note:** Coerce/sanitize `brandColor` server-side to `#[0-9A-Fa-f]{6}` (or reject) to prevent style injection; fall back to a neutral color when invalid.

### A.3 Tailwind, Typography & Components (MUST)

Use Tailwind utilities.
Headings: `text-2xl` (page), `text-xl` (section), `text-lg` (drawer).
Buttons: primary `bg-[color:var(--brand)] text-white rounded-2xl px-4 py-2`; destructive `bg-red-600 text-white rounded-2xl px-4 py-2`.
Cards: `rounded-2xl shadow-sm ring-1 ring-black/5 bg-white`.
Inputs: `border rounded-xl px-3 py-2 focus:ring-2 focus:ring-[color:var(--brand)]`.
Layouts: `max-w-3xl` public; `max-w-6xl` consoles.

**Build & static:** Tailwind builds to `public/css/app.css` (served at `/static/css/app.css`). **DoD:** `GET /` returns HTML referencing `/static/css/app.css` (200).

### A.4 Security & CSP (MUST)

Strict CSP; islands are page‑scoped scripts with hashes.
**CSRF** on merchant/admin POST forms only. Exempt public endpoints: `/checkout/:storeId`, `/status/:invoiceId`, `/w/*`, `/s/*`.

### A.5 Hydration Contract (MUST)

Each page that needs client behavior exposes `window.__PAGE__` before island scripts load.

```html
<script>
  window.__PAGE__ = <%- JSON.stringify(hydration || {}) %>;
</script>
```

**Never** include secrets. Islands read from `window.__PAGE__` only.
**Canonical hydration:** `{ invoiceId, magicLink, returnUrl, connectConfig? }`.

### A.6 Error Rendering (MUST)

* `error.ejs` exists and is used by the error middleware.
* Shows brand header + a friendly message + optional `error.code`.
* Never exposes stack traces in production.

---

## B. Public Surface Templates

### Magic Link — `GET /w/:storeId/:invoiceId?u=...(&return=...)`

**View**: `public/magic-link.ejs`
**Purpose**: Validate `u` server‑side, render a minimal page that immediately opens the wallet via Connect, and show state while polling status.

**Server validations (MUST, prior to render)** — see §5.1 steps 1–8.

**Props to view**: `branding`; `invoice` subset `{ invoiceId, amountSats, usdAtCreate, quoteExpiresAt, memo }`; `magicLink` (Pay Now Button); `returnUrl` (optional); `hydration: { invoiceId, magicLink, returnUrl?, connectConfig? }`.

**HTTP caching (MUST):** `Cache-Control: no-store, max-age=0`, `Pragma: no-cache`.

**UI contents (MUST)**: Amount, USD snapshot, Memo, Countdown, **QR = magic‑link**, Status strip, fallback **Open wallet** button.

**Islands**: `magic-link.ts` (Connect call using `u`; draws QR of the **current page URL**); `status-strip.ts` (polls `/status/:invoiceId`).

---

## C. Merchant Console Templates

**Base path:** `/merchant/:storeId`
**View**: `merchant/dashboard.ejs`

**Data**:

* High‑level metrics (optional in POC): count of unpaid/paid today, last invoices, upcoming subscriptions.

**UI**:

* KPI cards, recent items list, links to Invoices/Subscriptions.

---

### C.2 Invoices Ledger — `GET /merchant/:storeId/invoices`

**View**: `merchant/invoices.ejs`

**Data** (via WEBPAY → Bridge):

* List endpoint `GET /api/v1/stores/:storeId/invoices?status=…` (server-side fetch with filters).
* Per-row minimum fields: `invoiceId, createdAt, amountSats, usdAtCreate, status, txId?, memo, subscriptionId?`.

**UI**:

* Filters: status multi-select, date range, search by ID/memo.
* Table columns: *Invoice ID* · *Memo* · *Amount (sats)* · *USD at create* · *Created* · *Status* · *Tx* · *Actions*.
* Row actions by status:

  * `unpaid`: **Copy Link** · **Open Checkout** (opens `/w/...`) · **Cancel** · **Show QR** (POS style).
  * `paid`: **Refund** · **View Tx** · **Print/Receipt**.
  * `expired`: **Create New (prefill)** · **Archive**.
  * `canceled`: —
* **Invoice detail drawer**: `merchant/invoice-detail-drawer.ejs` with full DTO, history/webhooks summary.

**Refund flow (drawer)**:

* UI collects `{ amount_sats, memo }`.
* WEBPAY calls `POST /api/v1/stores/:storeId/refunds/create-tx` → unsigned `refund-invoice` → open wallet via Connect (island).
* Status updates reflected in ledger after broadcast.

**Cancel flow**:

* Preferred: `POST /api/v1/stores/:storeId/invoices/:invoiceId/cancel/create-tx` → sign via Connect.
* Fallback: `POST /api/v1/stores/:storeId/invoices/:invoiceId/cancel` → `{ canceled:true }`.

**DoD**:

* Table renders with at least one row and correct actions per status.

---

### C.3 Subscriptions Ledger — `GET /merchant/:storeId/subscriptions`

**View**: `merchant/subscriptions.ejs`

**Data** (via WEBPAY → Bridge):

* List fields: *Subscriber principal*, *Amount (sats)*, *Interval (blocks)*, *Status (Active/Cancelled)*, *Next due*, *Last billed*, *Mode (always `invoice`)*.

**UI**:

* Filters: Status, Next-due window (Overdue / Due soon / Scheduled), Text search.
* Row actions:

  * **Generate invoice now** → `POST /api/v1/stores/:storeId/subscriptions/:id/invoice` `{ ttl_seconds, memo? }`.
  * **Send email** → triggers WEBPAY emailer with the returned **magicLink**.
  * **Cancel subscription** → `POST /api/v1/stores/:storeId/subscriptions/:id/cancel` (may return unsigned cancel to sign via Connect).
  * **Details drawer** → schedule, history, linked invoices (with **Copy Link** and **Open invoice**).
  * **Manage subscription** (merchant-only) → renders `merchant/subscription-manage.ejs`.

**DoD**:

* Row action *Generate invoice now* produces a success sheet with **Amount**, **Expires**, **Copy Link**, **Send Email**.

---

### C.4 Settings (Branding/Profile) — `GET /merchant/:storeId/settings`

**View**: `merchant/settings.ejs`

**Data**:

* `GET /api/v1/stores/:storeId/profile` (server-side fetch) + live **public preview** using `GET /api/v1/stores/:storeId/public-profile`.

**UI**:

* Form: `{ displayName, brandColor, allowedOrigins, webhookUrl }`.
* Live preview panel renders **the same header/footer** components as public pages with the current inputs applied (client-only preview; no save yet).
* Save → `PATCH /api/v1/stores/:storeId/profile`.

**DoD**:

* Editing brand color updates preview instantly; saving persists and page reload shows updated brand.

---

### C.5 API Keys (Rotate) — `GET /merchant/:storeId/keys`

**View**: `merchant/api-keys.ejs`

**Data**:

* Buttons wired to WEBPAY server actions that call Bridge:

  * `POST /api/v1/stores/:storeId/rotate-keys` (or admin variant) → `{ apiKey, hmacSecret }` **one-time reveal**.

**UI**:

* Dangerous-area card with explicit warnings.
* On success: masked fields with **Copy** buttons; persistent note that secrets won’t be shown again.

**DoD**:

* After reveal, leaving the page and returning shows secrets **hidden**.

---

### C.6 POS — `GET /pos/:storeId`

**View:** `merchant/pos.ejs` (under `src/server/views`)
**Purpose:** Merchant-facing POS to create a short-TTL invoice, display **QR = magic-link**, poll status, and allow operator cancel.

**Props (server → view):**

* `branding` (from `GET /api/v1/stores/:storeId/profile`)
* `storeId` (from route param)
* Optional UI defaults: `{ defaultTtlSeconds, defaultAmountSats }`

**Hydration (`window.__PAGE__`):**

```ts
{ storeId: string }
```

*(POS islands never include secrets or Bridge data.)*

**Islands:**

* `pos.ts` — handles create/cancel flows and QR rendering.
* `status-strip.ts` — reused status bar that polls `/status/:invoiceId`.

**UI (SSR skeleton):**

* **New Sale card**

  * Inputs: **Amount (sats)**, Tip (%) or absolute (defaults to 0, should be easily skippable)
  **TTL** (default 2–5 min) and **Memo (text)** should be defaultet (300 seconds, "payment to <merchant name> <date>)
  * **Create** button (disabled while submitting)

* **Payment card** (shown after create)
  * **QR of `magicLink`** (exact string returned by WEBPAY → Bridge)
  * Invoice info: `invoiceId`, amount, memo
  * **Countdown** to `quoteExpiresAt`
  * **Status strip** (polls `/status/:invoiceId`)
  * **Cancel** button (operator)

**Interactions (browser → WEBPAY only):**

1. **Create invoice**
   * `POST /merchant/stores/:storeId/prepare-invoice` with `{ amount_sats, ttl_seconds, memo }`
   * **WEBPAY server** calls Bridge `POST /api/v1/stores/:storeId/prepare-invoice` with `X-API-Key`
   * Response `{ invoice, magicLink }` → render QR (exact `magicLink`), start countdown, start polling
2. **Poll status**
   * Every ~1200ms: `GET /status/:invoiceId`
   * Terminal states:
     * `paid` → stop, show **Paid ✓** with short `txId`, show **New sale**
     * `expired` or `canceled` → stop, show **Not paid**, show **Create new invoice** (prefill)
3. **Cancel unpaid (operator)**
   * Preferred on-chain:
     * Once Cancel is clicked in the UI WEBPAY Calls Bridge's `POST /merchant/stores/:storeId/invoices/:invoiceId/cancel/create-tx`

**Error states (rendered as banners/toasts in POS UI):**
* Validation errors on create (amount ≤ 0, TTL out of range)
* Bridge rejections mapped to friendly copy (e.g., `invalid_state`, `store_inactive`)
* Network errors: show retry on create/poll/cancel
* Countdown reaches zero before payment → auto-flip to **Expired**

**CSP & caching:**
* Page served with standard strict CSP; islands are page-scoped with hashes
* `Cache-Control: no-store` (POS should not be cached by intermediaries)

**DoD (for POS page):**
* Creating an invoice shows **QR = magic-link**, visible `invoiceId`, and a ticking countdown
* Status strip transitions through states and stops on terminal
* **Cancel** works via create-tx (when available) and via DTO fallback
* **Network tab** shows only `https://example.webpay.com/*`; **no** calls to Bridge from the browser

---

## D. Admin Console Templates

**Base path:** `/admin`

**Shell**: `admin/layout-admin.ejs` with admin nav.

### D.1 Admin Home — `GET /admin`

**View**: `admin/admin-home.ejs`

* Cards linking to *Stores*, *Keys*, *sBTC Token*, *Poller*, *Webhooks*.

### D.2 Stores — `GET /admin/stores`

**View**: `admin/stores.ejs`

**UI**:

* Create store form (`principal, name, display_name, logo_url, brand_color, allowed_origins, webhook_url`).
* Table of stores with **Activate/Deactivate** toggle (calls `PATCH /api/admin/stores/:storeId/activate`).

### D.3 Keys — `GET /admin/keys`

**View**: `admin/keys.ejs`

**UI**:

* **Rotate keys** for a store → `POST /api/admin/stores/:storeId/rotate-keys` → one-time reveal `{ apiKey, hmacSecret }`.

### D.4 sBTC Token Config — `GET /admin/token`

**View**: `admin/token-config.ejs`

**UI**:

* Form `{ contractAddress, contractName }` → `POST /api/admin/set-sbtc-token` returns unsigned call → open wallet via Connect (island) → show configured state.

### D.5 Poller & Webhooks — `GET /admin/poller`, `GET /admin/webhooks`

**Views**: `admin/poller.ejs`, `admin/webhooks.ejs`

**UI**:

* Poller status card `{ running, lastRunAt, lastHeight, lastTxId, lagBlocks }` with **Restart** action.
* Webhooks table with filter `status=all|failed&storeId=…`, and **Retry** action (`POST /api/admin/webhooks/retry`).

**DoD**:

* Restart reflects `running:true` and updates last run time.

---

## E. Template Data Contracts (Page Props)

```ts
// Shared
export type Branding = {
  displayName: string;
  logoUrl?: string | null;
  brandColor?: string | null;
  supportEmail?: string | null;
  supportUrl?: string | null;
};

// Public invoice DTO (normalized)
export type InvoiceDTO = {
  invoiceId: string;
  storeId: string;
  status: 'unpaid'|'paid'|'partially_refunded'|'refunded'|'canceled'|'expired';
  amountSats: number;
  usdAtCreate?: string;
  quoteExpiresAt?: string; // ISO
  merchantPrincipal?: string;
  payer?: string | null;
  txId?: string | null;
  memo?: string | null;
  subscriptionId?: string | null;
  createdAt?: string;
  refundAmount?: number | null;
  refundTxId?: string | null;
  store?: Pick<Branding,'displayName'|'logoUrl'|'brandColor'>;
};

export type MagicLinkViewProps = {
  branding: Branding;
  invoice: Pick<InvoiceDTO,'invoiceId'|'amountSats'|'usdAtCreate'|'quoteExpiresAt'|'memo'>;
  magicLink: string; // for QR
  returnUrl?: string | null;
  hydration: { invoiceId: string; magicLink: string; returnUrl?: string | null; connectConfig?: Record<string, unknown> };
};

export type InvoiceViewProps = {
  branding: Branding;
  invoice: InvoiceDTO;
  hydration: { invoiceId: string };
};

export type MerchantShellProps = { branding: Branding; nav: string; user: { name?: string; email?: string } };
```

---

## F. Sample EJS Skeletons

### F.1 Base Layout — `layouts/base.ejs`

```ejs
<!doctype html>
<html lang="en" class="h-full">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title><%= (title || branding.displayName) %></title>
  <link rel="stylesheet" href="/static/css/app.css" />
  <style>:root{ --brand: <%- branding.brandColor || '#111827' %>; }</style>
</head>
<body class="min-h-full bg-gray-50 text-gray-900">
  <%- include('../_partials/header', { branding }) %>
  <main class="mx-auto max-w-3xl px-4 py-6">
    <%- body %>
  </main>
  <%- include('../_partials/footer', { branding }) %>
</body>
</html>
```

### F.2 Magic Link — `public/magic-link.ejs`

```ejs
<% layout('../layouts/base') %>
<section class="space-y-4">
  <header>
    <h1 class="text-2xl font-semibold">Pay invoice <span class="text-gray-500">#<%- invoice.invoiceId %></span></h1>
    <p class="text-gray-600"><%= invoice.memo || '' %></p>
  </header>
  <div class="grid gap-4 md:grid-cols-2">
    <article class="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-black/5">
      <div class="text-sm text-gray-500">Amount</div>
      <div class="text-3xl font-bold"><%- invoice.amountSats %> sats</div>
      <% if (invoice.usdAtCreate) { %>
        <div class="text-gray-500 mt-1">≈ $<%- invoice.usdAtCreate %> at create</div>
      <% } %>
      <div id="countdown" class="mt-3 text-sm"></div>
      <div id="wallet-cta" class="mt-4">
        <button id="open-wallet" class="bg-[color:var(--brand)] text-white rounded-2xl px-4 py-2">Open wallet</button>
      </div>
    </article>
    <article class="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-black/5 flex items-center justify-center">
      <canvas id="qr" class="h-48 w-48"></canvas>
    </article>
  </div>
  <%- include('../_partials/status-strip') %>
</section>
<script>window.__PAGE__ = <%- JSON.stringify(hydration) %>;</script>
<script src="/static/js/magic-link.js" type="module"></script>
<script src="/static/js/status-strip.js" type="module"></script>
```

### F.3 Error — `error.ejs`

```ejs
<% layout('./layouts/base') %>
<section class="max-w-xl mx-auto">
  <div class="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-red-200">
    <h1 class="text-xl font-semibold text-red-700">We couldn’t process your request</h1>
    <p class="mt-2 text-gray-700"><%- error?.message || 'An unexpected error occurred.' %></p>
    <% if (error?.code) { %>
      <p class="mt-2 text-xs text-gray-500">Code: <%- error.code %></p>
    <% } %>
  </div>
</section>
```

---

## G. Controller Responsibilities (per view)

* **All public pages** fetch `branding` (public‑profile) and pass it to views.
* **Magic‑link** controller performs all server‑side checks (see §5.1) **before** rendering; on failure, render `error.ejs`.
* **Invoice** controller fetches the invoice DTO from Bridge and passes it through; failures render `error.ejs`.
* **Merchant/Admin** controllers fetch **profile** for branding and any per‑page lists (invoices, subscriptions, stores, etc.).

---

## H. Islands Behavior (JS)

* **`magic-link.ts`**

  * On load, attempts to call wallet via Connect using `u`.
  * If automatic open fails, shows the **Open wallet** button.
  * Draws QR of the **current page URL** (the magic‑link).
  * Renders countdown from `invoice.quoteExpiresAt` when provided.

* **`status-strip.ts`**

  * Poll every **1000–1500ms**; set `Accept: application/json`; abort on navigation or when the page is hidden (`document.hidden`).
  * Updates UI: *Awaiting payment* → *Broadcast seen* → *Paid ✓* or *Expired/Canceled*.

* **`pos.ts`**

  * Handles form submit to WEBPAY endpoint that calls Bridge `prepare-invoice`.
  * Shows QR, countdown, and Cancel flow.

---

## I. Acceptance Checks (Definition of Done)

1. **Views exist** for all routes listed in this document.
2. **Branding appears** on every page (logo/name + brand color applied to primary button and accents).
3. **Magic‑link**: with a valid `u`, page auto‑opens wallet and polls status; with invalid/expired `u`, page renders `error.ejs` and **no** wallet button.
4. **Invoice preview**: shows current status and updates live via polling.
5. **Merchant invoices**: ledger table renders with correct actions per status; *Cancel* and *Refund* trigger the defined flows.
6. **POS**: clerk can create an invoice, see QR, and watch status transition to Paid/Expired/Canceled; Cancel works (on‑chain or DTO fallback).
7. **Admin keys**: rotate‑keys page reveals secrets once and never again on reload.
8. **CSRF**: public routes work without CSRF token; merchant/admin forms are protected.
9. **No Bridge calls from browser**; all Bridge access is server‑to‑server from WEBPAY.
   *CI runs `scripts/verify-no-bridge-in-client.mjs` and fails on `/api/v1|/api/admin|/i/|/create-tx` in `/src/client/**`.*

---

## J. Notes & Non‑Goals (POC)

* SSE is not used; polling only.
* No client‑side routing frameworks; SSR first with small islands.
* Styling aims for clean defaults; merchants can change **only** brand color and logo in POC.

---

# Minimal Tech Stack to Stand Up

* **Bridge**: Node.js (v18+), Express, TypeScript.
  * Handles invoice generation, refunds, subscriptions, wallet tx building, polling, and webhooks.

* **DB**: SQLite (file-based) with raw SQL.
  * Stores invoices, refunds, and subscriptions.

* **WEBPAY**: Node.js + ejs for page generation
  * **Merchant Dashboard**: React (or any SPA framework).
  * **Checkout Page**: Plain HTML   JS (lightweight, wallet-triggered).
  * **Admin Console**
  * **Merchant Console**
  * **Magic Link**

* **Infra**:
  * Single public **HTTPS server** that:
    * Serves dashboard   checkout frontend
    * Hosts all API endpoints
    * Handles scheduled tasks (subscriptions)
  * **Environment-based configuration**:
    * Stacks network (testnet/mainnet)
    * Contract addresses
    * Webhook signing secret
    * Pricing API keys (if used)

* **Dev/Prod Environments**:
  * Start with **Stacks Testnet** (for wallet   contract testing)
  * Transition to **Stacks Mainnet** for production launch

Got it 👍 — here’s a short **developer notes** doc, meant as a peer-to-peer pointer list for someone experienced but overlooking some critical Clarity gotchas.

---

# 📝 Developer Notes

### 1. **Broken list zipping**

* **Issue**: `zip-actions` relies on fake closure-style accessors → always panics.
* **Fix**: Write a real zipper with recursion over the three lists, or prebuild tuples before storage.
* **Principle**: Clarity has no closures or implicit scope capture; pass lists explicitly.

---

### 2. **Incorrect unwrap layering**

* **Issue**: Calling `unwrap-panic` on `uint` values returned from helpers.
* **Fix**: `get-total-supply!` and `get-balance!` already unwrap → don’t double unwrap.
* **Principle**: Respect return types (`response` vs raw `uint`) to keep type safety clean.

---

### 3. **Execution flow type mismatch**

* **Issue**: `fold` uses `bool` accumulator but returns `(err ...)`. Won’t type-check.
* **Fix**: Use `(response bool uint)` as accumulator and short-circuit on first error.
* **Principle**: In Clarity, `fold` must unify types across all branches.

---

### 4. **Transfers from wrong principal**

* **Issue**: `stx-transfer?` uses `tx-sender` (caller) → drains executor’s STX, not DAO’s.
* **Fix**: Wrap in `(as-contract ...)` to use the contract’s balance.
* **Principle**: DAO/timelock execution should always act as the contract principal.

---

### 5. **SIP-010 trait over-strictness**

* **Issue**: Defined `transfer` with memo → won’t match canonical SIP-010 tokens.
* **Fix**: Define minimal trait with only the methods you actually call (`get-balance`, `get-total-supply`).
* **Principle**: Keep traits minimal to maximize compatibility.

---

### 6. **Lifecycle edge cases**

* **Queue**: No guard against already executed proposals.
* **Cancel**: Allows cancel after execution.
* **Fix**: Add explicit asserts.
* **Principle**: State machine transitions must be total and mutually exclusive.

---

### 7. **Governance design debt**

* **No voting snapshot** → Sybil via token splitting. Needs token-side checkpoints.
* **Admin hot-swap token** → can undermine governance. Add timelock or restrict.
* **Fix**: Document limitations and tighten admin functions.
* **Principle**: Governance contracts must be non-mutable or changes must themselves go through governance.

---

### 8. Missing Refund Limit Checks

* Issue: Refunds are not capped by original invoice amount.
* Fix: Enforce `refund-amount + refund-request <= invoice amount` **in Clarity** to ensure refunds never exceed funds received.
* Principle: Prevent merchant or contract from over-refunding or draining unexpected funds.

---

✅ **Summary:** Core technical debt is around Clarity semantics (lists, fold typing, contract principal) and governance hygiene (snapshots, admin powers). Fix those and the contract will both compile cleanly and behave as intended in real deployments.

---

Do you want me to consolidate these notes into an **action checklist** (like a punch-list the dev can tick off), or keep it as this higher-level “awareness memo”?

---

# 👨‍💻 Developer Implementation Details

This section provides **clarity-focused guidance** for implementing the sBTC Gateway. All backend and frontend development assumes full familiarity with Node, Express, and React. The focus here is to surface **critical implementation notes, contract caveats, and event-driven integration patterns** that are unique to Clarity and the Stacks blockchain.

---

## 🔐 Smart Contract – `contracts/sbtc-gateway.clar`

### 📌 Core Contract Design Principles

- **State lives on-chain**: Invoices are stored in a `map`, keyed by a 32-byte invoice ID.
- **Execution is transactional**: Clarity runs synchronously during transaction execution; there is no async, no future effects.
- **No looping, recursion only**: Clarity disallows loops. Write recursive functions when list iteration is needed.
- **Immutable logic, predictable execution**: All control flow must be explicit. Design for auditability and determinism.

---

### 🔧 Functions Overview

All contract interactions happen via **public functions**, each executing within a single transaction. They **must not depend on backend state or external APIs**.

#### ✅ `create-invoice`

- Called by the **merchant** (or contract owner for controlled deployments).
- Stores invoice in the `invoices` map with fields:
  - `merchant`: recipient of payment.
  - `amount`: amount in sats.
  - `expires-at`: optional block height expiry.
  - `memo`: optional metadata.
  - `paid`: initialized to `false`.
- Emits event: `invoice-created`.
- Rejects creation if `id` already exists (checked using `map-get?`)

#### ✅ `pay-invoice`

- Called by **payer (customer)** via wallet.
- Validates:
  - Invoice exists.
  - Not already paid or canceled.
  - Not expired (`expires-at` check).
  - If `expires-at` is present, ensure `block-height < expires-at`.

- Transfers sBTC using `contract-call?` to SIP-010 `transfer?`.
- Marks `paid = true`, records `payer`.
- Emits event: `invoice-paid`.
- Reject if:
  - Invoice already marked paid
  - Invoice is expired
  - Invoice is canceled

#### ✅ `refund-invoice`

- Called by the **merchant** (must match `merchant` on invoice).
- Only valid if invoice was paid.
- Transfers sBTC **from merchant to payer** via `transfer?`.
- Emits event: `invoice-refunded`.
- Reject if:
  - Refund exceeds remaining invoice amount (enforced in contract)
  - Invoice is not yet paid

#### ✅ `cancel-invoice`

- Callable by **merchant or admin**, but only if **not paid**.
- Prevents invoice from being paid.
- Use case: stale or spam invoices.

---

### 📌 Event Strategy

Events are emitted using `print` statements with JSON-style objects. These are surfaced via Stacks API as `contract_log` entries.

> Example event:
```clarity
(print { event: "invoice-paid", id: id, payer: tx-sender, amount: amount })
```

Use event fields to **index invoice activity** and drive webhook triggers.

---

### 📑 Data Structure

```clarity
(define-map invoices
  { id: (buff 32) }
  {
    merchant: principal,
    amount: uint,
    payer: (optional principal),
    memo: (optional (buff 34)),
    paid: bool,
    expires-at: (optional uint),
    canceled: bool,
    refund-amount: uint ; ✳️ Track cumulative refunds (prevents over-refund)
  }
)

  (define-map subscriptions
    { id: (buff 32) }
    {
      merchant: principal,
      amount: uint,
      interval: uint,
      last-paid: (optional uint),
      active: bool
    }
  )

```

Use `map-get?` and `merge` to safely update entries.

---

### ✅ SIP-010 Integration

To send sBTC, the contract **delegates to the external sBTC contract** via:

```clarity
(contract-call? sbtc-token transfer? amount tx-sender merchant none)
```

- `sbtc-token` is settable by admin (`set-sbtc-token`) and implements a **minimal trait**:
```clarity
(define-trait ft-transfer-only
  (
    (transfer? (uint principal principal (optional (buff 34))) (response bool uint))
  )
)
- This avoids compatibility issues with overly strict SIP-010 traits.

---

### 🛡️ Security Model

- **Authority**:
  - Only contract owner can `create-invoice` and `cancel-invoice`.
  - Only merchant can `refund-invoice`.

- **State Safety**:
  - Prevents double-pay via `paid` flag.
  - Rejects payment if invoice is `expired` or `canceled`.
  - Validates refund amount against original invoice (optional: enforce in contract or backend).

- **Principal Management**:
  - Use `tx-sender` to authorize users.
  - Use `(as-contract ...)` only if contract is meant to hold/transfer its own funds (not needed here).

---

### 💡 Clarity Design Tips

#### ✅ Use `(some <val>)` consistently for optional types
Ensure `payer` is wrapped in `(some tx-sender)` upon setting.

#### ✅ Match every branch
Always exhaust all possible branches in `(match)` and `(if)` expressions. Don't assume a field will always be present.

#### ✅ Prefer `(ok true)` over side effects
Contracts should return clean success/failure codes — don’t encode logic in error messages.

#### ✅ Buffers must be exactly sized
If using `(buff 32)` for IDs, client-side IDs **must be padded or truncated** correctly when passed to Clarity.

---

## 🔗 Wallet Interactions

### `pay-invoice`

- Users initiate a transaction that calls `pay-invoice(id)`.
- Must include **post-condition**:
  - ≥ `amountSats` of sBTC leaves wallet.
- Args:
  - `id: (buff 32)`.

### `refund-invoice`

- Merchants initiate from dashboard.
- Wallet must include post-condition to ensure no more than the intended `amount` of sBTC is transferred.
- Contract ensures `tx-sender == merchant`.
- Args:
  - `id: (buff 32)`
  - `amount: uint`
  - `memo: optional (buff 34)`

---

## 🧠 Best Practices

| Concern                   | Practice                                                                 |
|---------------------------|--------------------------------------------------------------------------|
| Invoice ID format         | Always generate UUID → truncate to 32-byte hex for Clarity buffer.      |
| sBTC precision            | Store and transfer in satoshis (integer only).                          |
| USD pricing               | Use USD snapshot only for display; **sats are source of truth**.        |
| Expiration enforcement    | Optional field (`expires-at`) is used **only if present**.              |
| Refund amount validation  | **Enforced in contract**: cumulative `refund-amount + amount ≤ original amount`. |
| Webhook timing            | Backend triggers webhooks after event is detected, not from Clarity.    |
| Subscription mode         | Canonical path is **invoice-per-period**; `pay-subscription` is optional direct mode and must be explicitly configured per subscription. |

---

## 🧪 Testing & Dev

| Activity            | Recommendation                                 |
|---------------------|------------------------------------------------|
| Testnet setup       | Deploy contract to Stacks testnet              |
| Integration test    | Use `clarinet integrate` or custom test suite  |
| Debug events        | Use Hiro Explorer contract logs panel          |
| Test wallet flows   | Use Hiro Wallet in test mode                   |
| Contract upgrade    | Not supported directly — deploy v2 and update backend |

---

## ✅ Recap: Clarity Implementation Coverage

| Function              | Description                                  |
|-----------------------|----------------------------------------------|
| `create-invoice`      | Admin creates invoice                        |
| `pay-invoice`         | Customer pays invoice                        |
| `cancel-invoice`      | Admin cancels unpaid invoice                 |
| `refund-invoice`      | Merchant refunds customer                    |
| `set-sbtc-token`      | Set external sBTC contract reference         |
| `get-invoice`         | Read-only, returns invoice data              |
| `is-paid`             | Read-only, true if invoice is marked paid    |
| `get-sbtc`            | Read-only, returns sBTC contract principal   |

---

## 🛠️ DevTool Reference

| Tool/Lib               | Usage                                                        |
|------------------------|-------------------------------------------------------------|
| [`@stacks/transactions`](https://www.npmjs.com/package/@stacks/transactions) | Build & sign txs in JS (contract calls, post-conditions) |
| [Stacks Wallet](https://wallet.hiro.so)         | Test contract interactions from user wallet                |
| [Stacks API](https://docs.hiro.so/stacks-api/)  | Poll contract logs, tx status, block height               |
| [Clarinet](https://docs.stacks.co/write-smart-contracts/clarinet/overview) | Test & dev Clarity contracts locally                     |

---

✅ Your backend handles the business logic.

✅ Your smart contract handles payment safety, transfer rules, and state enforcement.

✅ The contract is minimal but production-capable — perfect for low-friction commerce on Stacks.

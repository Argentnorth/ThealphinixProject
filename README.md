# SupportPilot — Customer Email Support Automation

SupportPilot turns incoming customer email into an accountable support workflow: messages are parsed and threaded into tickets, categorized and prioritized, matched to approved response policies, queued for durable delivery, and recorded in an audit trail. The workspace contains a Flask API and a React/Vite operations dashboard.

## What is included

- **Ticket lifecycle:** threaded inbound/outbound conversations, customer records, assignment, status, priority, SLA due dates, reopen-on-reply, and attachment storage.
- **Email integration boundaries:** IMAP polling with commit-before-seen semantics, RFC 5322/MIME parsing, duplicate protection by `Message-ID`, SMTP STARTTLS or a safe console transport, and a database-backed outbox with retries.
- **Automation:** explainable deterministic classification by default, optional local scikit-learn model loading, approved-template auto-response gates, acknowledgement messages, and review-only AI draft support through an explicitly configured organization endpoint.
- **Controls:** agent/admin JWT roles, template versioning and approval, append-only audit records, SLA escalation, safe local demo data, and operational controls.
- **Dashboard:** login, queue filters, threaded ticket workspace, reply drafting, template library/editor, operational reporting, user administration, and manual job controls.

## Architecture

```text
frontend/                         React/Vite dashboard
backend/support_automation/
  api/                            Auth, tickets, templates, reports, admin HTTP boundaries
  services/
    ingestion.py                  IMAP adapter; persists before marking source mail seen
    mime_parser.py                RFC 5322/MIME normalization and attachment extraction
    ticketing.py                  Threading, triage, ticket lifecycle, attachment/outbox staging
    triage.py                     Rule baseline and optional local ML classifier
    response_builder.py           Approved template renderer and review-only draft generator
    mail.py                       Durable outbox delivery and SLA escalation
    reporting.py                  Dashboard metrics
  models.py                       Users, customers, tickets, messages, templates, audit, outbox
  security.py                     JWT identity resolution and role policy
  jobs.py                         Optional APScheduler jobs
```

### Core entities

| Entity | Purpose |
| --- | --- |
| `User` | Authenticated agent or administrator. |
| `Customer` | Canonical customer email identity and profile. |
| `Ticket` | Support case with owner, status, category, priority, and SLA deadline. |
| `Message` | Inbound or outbound message linked with RFC thread headers. |
| `Attachment` | File metadata and controlled local storage location. |
| `Category` | Routing policy, confidence threshold, and default SLA. |
| `Template` / `TemplateVersion` | Approved response policy with immutable version history. |
| `OutboundDelivery` | Durable queued/retry/sent/failed delivery state. |
| `AuditLog` | Append-only support and control-plane activity trail. |

## Quick start: local development

Prerequisites: Python 3.11+ and Node.js 20+.

```bash
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
flask --app backend/wsgi.py seed-demo
```

Run the API in one terminal:

```bash
. .venv/bin/activate
flask --app backend/wsgi.py run --debug
```

Run the dashboard in another terminal:

```bash
npm install --prefix frontend
npm run dev --prefix frontend
```

Open `http://localhost:5173`. The explicit local demo seed creates:

| Role | Email | Password |
| --- | --- | --- |
| Administrator | `admin@support.local` | `Admin@12345` |
| Agent | `agent@support.local` | `Agent@12345` |

These passwords are for local demonstration only. Do not seed this data or use these credentials in a real deployment.

The default `MAIL_TRANSPORT=console` performs no external delivery. Outbound replies appear in server logs and are marked as delivered when the scheduler runs or when an administrator clicks **Deliver outbox**.

## Docker Compose

1. Copy and update the environment file: `cp .env.example .env`
2. Replace secret values and configure SMTP/IMAP only when ready.
3. Start the stack:

```bash
docker compose up --build
```

The dashboard is available at `http://localhost:8080`; the API is available at `http://localhost:5000/api/v1/health`.

For a fresh container database, seed reference/demo data from the backend container:

```bash
docker compose exec backend flask --app wsgi.py seed-demo
```

## Mail integration safety boundary

1. Set `IMAP_ENABLED=true` and configure the IMAP variables. The poller fetches unseen mail with UID commands, persists an intake decision or ticket/message transaction, then marks the source message seen. Replays are harmless because `Message.rfc_message_id` and the inbound-intake Message-ID are unique.
2. Set `MAIL_TRANSPORT=smtp` and configure the SMTP variables to enable external delivery. The application uses SMTP STARTTLS when `SMTP_USE_STARTTLS=true`.
3. Fill in the **Tell SupportPilot what your company does** prompt after the first administrator login. The company brief is stored server-side and is the only business context supplied to AI generation.
4. Groq intake is opt-in. Create a **new** Groq key after any accidental exposure, store it only in `GROQ_API_KEY`, set `GROQ_ENABLED=true`, and restart the backend. The browser never receives this key.
5. With a complete profile, enabled Groq, and high confidence, SupportPilot sends a grounded reply only for a genuine new customer request. Promotions are recorded without a ticket or reply; unclear mail becomes a human-review ticket. Existing conversation replies stay threaded and do not receive duplicate automated replies.
6. AI generation is fail-closed: missing configuration, provider errors, malformed output, and low confidence never produce an automatic email. All queued delivery still uses the durable outbox.

## HTTP API overview

All non-health endpoints are rooted at `/api/v1` and expect `Authorization: Bearer <access token>`.

| Area | Key endpoints |
| --- | --- |
| Authentication | `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me` |
| Tickets | `GET/POST /tickets`, `GET/PATCH /tickets/:id`, `POST /tickets/:id/assignment`, `POST /tickets/:id/reply`, `POST /tickets/:id/draft` |
| Templates and policy | `GET/POST /templates`, `PATCH /templates/:id`, `POST /templates/:id/approval`, `GET/POST/PATCH /categories` |
| Reporting | `GET /reports/overview`, `/reports/volume`, `/reports/workload` (admin) |
| Administration | `GET/POST/PATCH /admin/users`, `GET /admin/audit`, `GET /admin/operations`, and manual operation triggers |

## Validation commands

```bash
# Backend syntax check
python3 -m compileall -q backend

# Backend local database and demo flow
flask --app backend/wsgi.py seed-demo
flask --app backend/wsgi.py poll-mailbox
flask --app backend/wsgi.py deliver-outbox
flask --app backend/wsgi.py check-sla

# Frontend production build
npm run build --prefix frontend
```

## Production notes

- Set unique high-entropy `SECRET_KEY` and `JWT_SECRET_KEY`; never commit the `.env` file.
- Use PostgreSQL and a shared attachment volume/object-store adapter for multi-instance deployments.
- Run polling, outbox delivery, and SLA escalation in one dedicated worker process or a distributed scheduler; do not enable the in-process scheduler in every Gunicorn worker.
- Terminate HTTPS in the reverse proxy and restrict CORS to known dashboard origins.
- Add database migrations before changing a deployed schema.

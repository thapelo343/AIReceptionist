# Sending calls to Frappe CRM

Every finished call is POSTed once to the CRM as a signed CallResult (v1). The CRM shows it in the
inbox, creates/links the lead, stores a Call Log with transcript, and can send a WhatsApp/SMS follow-up.

## 1. In the CRM (Settings → Channels → Add Channel → Voice)
- **AI receptionist number**: the number callers dial, E.164 (`+27105550100`).
- **Webhook secret**: a long random string (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
- **Recording password**: another random string.
- **Recording server URL**: the public https address of the recording server (step 2). It must equal this
  service's `recordings_base_url` exactly (for example `https://voice.example.com/recordings`). Recordings
  only play when the two match, and the CRM sends the recording password only to that address.
- Optional **Follow-up channel** (WhatsApp needs an approved template with ≤ 2 placeholders).
- Copy the **Webhook URL** shown after saving.

## 2. In this service
- Business YAML: add the `crm:` block (see `config/businesses/example-dental.yaml`), `url` = Webhook URL.
  `recordings_base_url` must be https.
- `.env`: `CRM_SECRET_<NAME>` = Webhook secret; `CRM_RECORDINGS_PASSWORD` = Recording password.
- Recordings (local storage only): run `python -m receptionist.crm.recordings` behind HTTPS
  (e.g. Caddy `reverse_proxy localhost:8089`) on a **public** host — the CRM refuses private addresses.

## 3. Forward the business line to the AI receptionist
South African businesses forward their existing number to the AI receptionist number with GSM codes:
- `**004*<number>#` forwards missed calls only (recommended).
- `**21*<number>#` forwards all calls.
- `##002#` switches forwarding off.

The CRM's Voice channel page shows these codes with Copy and Dial buttons.

## 4. Failures
Undeliverable results are written to `<messages dir>/.failures/crm-<call_id>.json`. 4xx responses are not
retried (fix the config, then re-POST the archived `payload` with the same signature scheme).

## Outcomes
`transfer` > `message` > `after_hours` (call started outside `hours`) > `hangup`.

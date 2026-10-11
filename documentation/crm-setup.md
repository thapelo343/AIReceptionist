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
  The server listens on localhost only (`CRM_RECORDINGS_HOST`, default `127.0.0.1`) behind the HTTPS proxy.
- Recording paths must line up, or calls reach the CRM without a recording (the agent logs
  `CRM call result <call_id> sent without recording: <reason>`):
  - `crm.recordings_root` must contain `recording.storage.local.path` (the egress output folder).
  - `CRM_RECORDINGS_ROOT` (the recording server's root) must equal `crm.recordings_root`.
  - Local recordings need **self-hosted LiveKit egress** on the voice host. With LiveKit Cloud egress the
    files are written on LiveKit's side, not on the voice host, so there is nothing to serve.

## 3. Forward the business line to the AI receptionist
South African businesses forward their existing number to the AI receptionist number with GSM codes:
- `**004*<number>#` forwards missed calls only (recommended).
- `**21*<number>#` forwards all calls.
- `##002#` switches forwarding off.

The CRM's Voice channel page shows these codes with Copy and Dial buttons.

## 4. Failures
Before the first POST, each result is written to `<messages dir>/.failures/pending-<call_id>.json`; the file
is deleted once the CRM accepts it. If the job is cut off mid-delivery, the pending file stays. Undeliverable
results (4xx, which are not retried, or 5xx/network after 3 attempts) move to `.failures/crm-<call_id>.json`
with the error.

After fixing the cause (secret, URL, CRM channel settings), resend them:

```bash
python -m receptionist.crm.replay --config config/businesses/<business>.yaml --all
python -m receptionist.crm.replay --config config/businesses/<business>.yaml messages/<business>/.failures/crm-<call_id>.json
```

It prints one line per file (`ok`, or `failed: <reason>`), deletes files that were delivered and keeps the rest.
A `crm-*.json` whose error starts with `build_call_result failed` holds only the call id and cannot be
resent; check the agent log for that call.

## Outcomes
`transfer` > `message` > `after_hours` (call started outside `hours`) > `hangup`.

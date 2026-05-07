# TG Integration: amoCRM Chats webhook

Minimal service for amoCRM Chats API channel connection and outbound message webhooks from amoCRM.

## Included

- `POST /webhooks/amocrm/chats/{scope_id}` - public webhook endpoint for `https://domain.com/webhooks/amocrm/chats/:scope_id`.
- Incoming webhook `X-Signature` verification with `HMAC-SHA1` over the raw request body and channel secret.
- `POST /amocrm/chats/connect` - backend helper for connecting an amoCRM account to the registered channel and receiving `scope_id`.
- `POST /webhooks/telegram` - Telegram Bot API webhook endpoint.
- `POST /telegram/webhook/setup` - helper that calls Telegram `setWebhook`.
- `python -m tg_integration.telegram_polling` - Telegram `getUpdates` polling mode for setups without a public HTTPS domain.
- Text bridge in both directions: Telegram user messages go to amoCRM, amoCRM manager messages go back to Telegram.
- SQLite state for conversation mapping, processed webhook ids, and message ids.
- Signed outbound requests to `amojo` with `Date`, `Content-Type`, `Content-MD5`, and `X-Signature`.

## Setup

1. Register a chat channel with amoCRM support. Use this webhook URL in the request:

   ```text
   https://your-domain.com/webhooks/amocrm/chats/:scope_id
   ```

2. Copy `.env.example` to `.env` and fill values issued by amoCRM:

   ```text
   AMOCRM_CHAT_CHANNEL_ID=...
   AMOCRM_CHAT_CHANNEL_SECRET=...
   AMOCRM_CHAT_SCOPE_ID=...
   AMOCRM_CHAT_BASE_URL=https://amojo.amocrm.ru
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_WEBHOOK_SECRET=generate-a-random-32-char-string
   PUBLIC_BASE_URL=https://your-domain.com
   ```

3. Get the account `amojo_id` using one of the documented amoCRM options:

   ```js
   APP.constant('account').amojo_id
   ```

   or via amoCRM API:

   ```text
   GET /api/v4/account?with=amojo_id
   ```

4. Connect the account to the channel:

   ```bash
   curl -X POST http://localhost:8000/amocrm/chats/connect \
     -H "Content-Type: application/json" \
     -d '{"account_id":"amojo-account-id","title":"TG Integration","hook_api_version":"v2"}'
   ```

   amoCRM returns `scope_id`. This value is substituted into the webhook URL instead of `:scope_id`.

## Run

Docker Compose:

```bash
cp .env.example .env
docker compose up -d --build
```

Use `TG_INTEGRATION_PORT` to publish the container on a different host port:

```bash
TG_INTEGRATION_PORT=8001 docker compose up -d --build
```

Local Python:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn tg_integration.main:app --app-dir src --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

Set Telegram webhook:

```bash
curl -X POST http://localhost:8000/telegram/webhook/setup \
  -H "Content-Type: application/json" \
  -d '{"drop_pending_updates":true}'
```

You can also pass a one-off URL:

```bash
curl -X POST http://localhost:8000/telegram/webhook/setup \
  -H "Content-Type: application/json" \
  -d '{"public_base_url":"https://your-domain.com","drop_pending_updates":true}'
```

Polling mode without a domain:

```bash
python -m tg_integration.telegram_polling --once
```

Run it continuously:

```bash
python -m tg_integration.telegram_polling
```

With Docker Compose:

```bash
docker compose --profile polling up -d --build
```

Polling calls Telegram `deleteWebhook` first because Telegram does not allow `getUpdates` while a webhook is active. Use `--drop-pending-updates` only when you want to discard queued Telegram messages.

Run tests:

```bash
pip install -r requirements-dev.txt
pytest
```

## Terms

- `channel_id` - channel ID issued by amoCRM after support registers the chat channel.
- `account_id` in `/amocrm/chats/connect` - the account `amojo_id`, not the regular numeric amoCRM account ID.
- `scope_id` - account-channel connection ID returned by the `connect` method. amoCRM substitutes it into the webhook URL.
- `TELEGRAM_WEBHOOK_SECRET` - random secret passed to Telegram `setWebhook` as `secret_token`. Telegram sends it back in `X-Telegram-Bot-Api-Secret-Token`; the service rejects webhook requests without the exact value.
- `getUpdates` - polling mode where this service asks Telegram for new bot messages. It does not need `PUBLIC_BASE_URL`, `TELEGRAM_WEBHOOK_SECRET`, a domain, or HTTPS, but it must be running constantly.

## Current bridge behavior

- Telegram conversation id in amoCRM is `tg:<telegram_chat_id>`.
- Telegram sender id in amoCRM is `tg_user:<telegram_user_id>`.
- amoCRM -> Telegram routing uses `message.conversation.client_id`; if it is `tg:<chat_id>`, the message is sent to that Telegram chat.
- Telegram text and captions are forwarded as text.
- amoCRM `picture`, `video`, `voice`, `audio`, and `file` messages are sent to Telegram using the media URL from amoCRM.
- Telegram media is not exposed to amoCRM by default because Telegram file URLs include the bot token. Set `TELEGRAM_EXPOSE_FILE_URLS=true` only if you explicitly accept that tradeoff.

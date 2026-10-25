# Nexora · Quick Codes Telegram Bot

A Telegram storefront for digital services powered by the Quick Codes API. The bot has its own MongoDB-backed user wallets, recharge flow, purchase history, and owner controls.

## What it offers

- ✈️ **Telegram accounts (Server 2):** displayed at the current provider rate, with **no additional reseller markup**.
- 💬 **WhatsApp accounts** and 📲 **apps OTP** through Server 3.
- 📈 **Social media services (SMM)** through the configured provider.
- 💳 Wallet recharge, order history, service toggles, API-key setup, balance checks, and owner-only health checks.
- 🛡️ Retry-safe refund handling for the covered cancellation/payment flows.

> **Product scope:** this repository currently sells provider-supplied Telegram accounts/activations. It is not yet a separate Telegram username/ID marketplace, and it does not compare multiple sellers. “No added markup” means provider-rate pricing from the configured Quick Codes API, not a guarantee that it is the cheapest rate across the internet.

## Setup

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Create a MongoDB database (MongoDB Atlas works).
3. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, and `QC_API_BASE`.
4. Install dependencies: `pip install -r requirements.txt`.
5. Run locally: `python bot.py`.
6. Open the bot in Telegram and send `/admin` as an owner.

## Owner setup

- **API keys:** configure the Server 2, Server 3, and SMM keys using the Quick Codes API keys supplied for your account.
- **Pricing:** configure supported markup for Server 3 and SMM. Telegram account pricing stays at the provider rate with no added markup.
- **Payments:** configure Auto UPI / UTR verification and/or manual UPI / QR, then enable the payment methods you intend to use.
- **Services:** switch supported services on or off for users.
- **Force join:** owners can configure up to two required channels in Telegram with `/setforcechannel 1 @channelusername` and `/setforcechannel 2 @channelusername`; remove with `/setforcechannel off 1` or `/setforcechannel off all`. The bot must be an administrator in each channel.
- **API balance:** monitor and recharge the Quick Codes wallet in the Quick Codes bot.
- **Health:** `/health` reports local database/configuration checks; it does not perform live payment verification or prove provider API availability.

Admin commands include `/admin`, `/health`, `/credit user_id amount`, and `/debit user_id amount`.

## Fast deployment checklist

1. Push the latest `main` branch to GitHub and create a Render **Background Worker** for this repository.
2. Set the build command to `pip install -r requirements.txt` and the start command to `python bot.py` (the repository `Procfile` also declares `worker: python bot.py`).
3. Add these required environment variables in Render: `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, and `QC_API_BASE`.
4. Optional: set `DB_NAME`, `BOT_TITLE`, and `SUPPORT_LINK`. Configure provider keys from the bot's owner panel after startup.
5. Deploy, then inspect logs for the line `is running`. In Telegram, send `/start` and (as an owner) `/health`.

**Important:** Telegram polling needs a continuously running worker. If your Render account does not offer a free Background Worker, a free Web Service is not a drop-in replacement for this polling setup. Do not put secrets in GitHub or send them in chat.

## Deployment

The bot supports both Telegram polling (local/worker mode) and Telegram webhooks (Render Web Service mode). On Render, `RENDER_EXTERNAL_URL` automatically selects webhook mode and the server binds to `PORT`. Set required environment variables in Render's Environment section; never commit `.env` or paste credentials into chats. Review startup logs after deploying.

## Operational notes

- Quick Codes API limits may apply; configure an appropriate limit in the Quick Codes bot for your expected traffic.
- If provider balance or service availability is insufficient, purchases may be unavailable.
- Refund retry safety exists for specific flows; it should not be interpreted as proof that every external payment/provider scenario has been tested live.
- Keep API keys, bot tokens, database URLs, and payment credentials private.

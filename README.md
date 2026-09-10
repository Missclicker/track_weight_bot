# track_weight_bot

A small Telegram bot for a friend group that wants to lose weight together. It lives in one group chat and:

1. **Counts calories from food photos** (Gemini vision, free tier) and logs them to a Google Sheet — with a separate alcohol counter.
2. **Records weight** when someone posts a number like `84.3` (or replies to the morning ping).
3. **Pings everyone who hasn't weighed in by 11:00.**
4. **Logs sport** from free text ("пробіг 5 км за 30 хв") as negative calories.
5. **Posts a weekly AI report** — intake, sport, weight trend, and recommendations (kcal target, veg/protein ratio).

Everything runs on free tiers: a Python bot with long polling (no public endpoint needed), Google Sheets as the database, Gemini Flash for AI. Estimated cost: **$0/month**. Bot replies are in Ukrainian by default (see `bot/i18n.py`).

> Calorie estimates from photos are rough (±30–50 %). The bot always says "≈" and you can correct any estimate by replying to it: a number sets the kcal directly, any other text ("це 300 г", "без хліба", "це солянка, а не борщ") makes Gemini re-estimate the dish, looking at the photo again.

---

## Setup checklist

You need your own accounts and keys for everything below. Nothing is shared — each deployment has its own bot, spreadsheet and API keys. Budget about **30–40 minutes** the first time.

- [ ] 1. Telegram bot token
- [ ] 2. Telegram group + privacy mode
- [ ] 3. Google Cloud project with the Sheets API and a service account
- [ ] 4. Google Spreadsheet shared with the service account
- [ ] 5. Gemini API key
- [ ] 6. Local run (`.env`, Python 3.12)
- [ ] 7. (Optional) Oracle Cloud Always Free VM for 24/7 hosting

### 1. Telegram bot token

1. In Telegram open [@BotFather](https://t.me/BotFather) → `/newbot`. Pick a display name and a username ending in `bot`.
2. Copy the token (`123456789:AA...`) → this is `TELEGRAM_BOT_TOKEN`.
3. Optional but recommended: `/setcommands` and paste

   ```t
   start - Зареєструватися і показати довідку
   w - Записати вагу: /w 84.3
   vaga - Те саме, що /w
   food - Записати їжу текстом: /food борщ і два хліба
   yizha - Те саме, що /food
   sport - Записати активність: /sport біг 5 км 30 хв
   today - Мій підсумок за сьогодні
   sohodni - Те саме, що /today
   week - Тижневий звіт зараз
   tyzhden - Те саме, що /week
   help - Довідка
   dovidka - Те саме, що /help
   ```

   Every command also has a Cyrillic spelling the bot understands when typed — `/вага`, `/їжа`, `/спорт`, `/сьогодні`, `/тиждень`, `/довідка`, `/старт` — but Telegram only allows `a-z 0-9 _` in registered commands, so those cannot go into `/setcommands` and won't autocomplete. The full alias table is `COMMANDS` in `bot/i18n.py`.

### 2. Telegram group + privacy mode

By default a bot in a group only sees commands, @mentions and replies to itself. To react to plain photos and numbers it must see every message:

1. In BotFather: `/mybots` → your bot → **Bot Settings → Group Privacy → Turn off**.
2. Add the bot to your group. **If the bot was already in the group before you changed privacy, remove and re-add it** — Telegram applies the setting only on join.
3. Get the group's chat id: send any message in the group, then open
   `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates` in a browser and copy `"chat":{"id":-100…}`. Alternatively run the bot once and read the id from the log. This is `ALLOWED_CHAT_IDS` (comma-separated if more than one group).

   The bot ignores every chat not in `ALLOWED_CHAT_IDS`, so strangers cannot spend your Gemini quota.

### 3. Google Cloud project, Sheets API, service account

1. Go to <https://console.cloud.google.com/> → create a project (e.g. `track-weight-bot`). A free account is enough; no billing needed for the Sheets API.
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google Drive API**.
3. **IAM & Admin → Service Accounts → Create service account**. Name it `weight-bot`, no roles needed.
4. Open the service account → **Keys → Add key → JSON**. Download the file and save it as `secrets/service_account.json` in the repo (this folder is git-ignored). Alternatively put its full content into `GOOGLE_SERVICE_ACCOUNT_JSON` as a single line.
5. Note the service account e-mail (`weight-bot@track-weight-bot.iam.gserviceaccount.com`) — you will share the spreadsheet with it.

### 4. Google Spreadsheet

1. Create a new Google Spreadsheet (any name, e.g. `Weight tracker`).
2. **Share** it with the service-account e-mail from step 3.5 as **Editor** (untick "notify").
3. Copy the id from the URL: `https://docs.google.com/spreadsheets/d/<THIS_PART>/edit` → `GOOGLE_SHEET_ID`.
4. You do **not** need to create sheets/tabs manually. On first start the bot runs `python -m bot.init_sheets` logic and creates the tabs `users`, `weight`, `food`, `sport`, `reports` with headers. Running `python -m bot.init_sheets` again is safe (idempotent).

Sheet layout (for reference / manual edits):

| tab       | columns                                                                                                                                             |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `users`   | `user_id, chat_id, name, username, tz, active, joined_at, height_cm, target_kg, daily_kcal_target`                                                  |
| `weight`  | `ts, date, user_id, name, kg, source`                                                                                                               |
| `food`    | `ts, date, user_id, name, dish, kcal, alcohol_kcal, protein_g, fat_g, carbs_g, veg_share, confidence, source, message_id, corrected, photo_file_id` |
| `sport`   | `ts, date, user_id, name, activity, minutes, distance_km, kcal, source`                                                                             |
| `reports` | `ts, week_start, chat_id, text`                                                                                                                     |

### 5. Gemini API key

1. Go to <https://aistudio.google.com/apikey> → **Create API key** (you can attach it to the same Google Cloud project).
2. Copy it → `GEMINI_API_KEY`.
3. Free tier as of September 2026: roughly 15 requests/min and ~1,500 requests/day on Flash / Flash‑Lite models, image input included. **Do not enable billing on that project** unless you want to pay — the free tier applies only while billing is off.
4. Free-tier caveat: Google may use free-tier prompts (your food photos) to improve its models, and the free tier is for non‑commercial use. Tell your group.
5. Default models are set in `.env.example` (`GEMINI_VISION_MODEL`, `GEMINI_TEXT_MODEL`). If a model name is retired, pick a current Flash model from <https://ai.google.dev/gemini-api/docs/models>.

### 6. Run locally

Requirements: Python 3.12+, or Docker.

```bash
git clone <your fork> track_weight_bot
cd track_weight_bot
cp .env.example .env          # fill in the values from steps 1–5
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m bot.init_sheets     # creates tabs, checks credentials
python -m bot                 # starts long polling
```

Sanity test: post a food photo in the group → the bot replies with an estimate and a row appears in `food`. Post `84.3` → row in `weight`. `/today` → your summary.

Run tests: `pytest` (pure parsing/MET/report logic plus routing tests through a real aiogram dispatcher with a mocked Telegram session - no network needed). Lint: `ruff check .`.

With Docker:

```bash
docker compose up -d --build
docker compose logs -f
```

### 7. Host it 24/7 on Oracle Cloud Always Free (optional)

Any Linux box with outbound internet works (home server, Raspberry Pi, any VPS). Oracle's Always Free tier gives a small VM permanently at no cost:

1. Sign up at <https://www.oracle.com/cloud/free/> (a card is required for identity check; Always Free resources are never billed — do **not** upgrade to Pay As You Go if you want a hard guarantee).
2. **Compute → Instances → Create instance**.
   - **Shape.** Two Always Free options:
     - **VM.Standard.A1.Flex** (tab *Ampere*, ARM): up to 4 OCPU / 24 GB in total, e.g. 2 OCPU / 12 GB. Often "out of host capacity" for free accounts — retry later or try another availability domain.
     - **VM.Standard.E2.1.Micro** (tab *Specialty and previous generation*, AMD x86): 1/8 OCPU, 1 GB RAM. Enough for this bot, which mostly waits on the network. Use it when A1 is not available.
   - **Image.** Ubuntu 24.04 or Oracle Linux 9 — commands for both are below. The console picks the architecture matching the shape (aarch64 for A1, x86_64 for E2.1.Micro); the Docker image is multi-arch, so both work.
   - **SSH key.** "Generate a key pair for me" is fine (Oracle does not keep the private half). Download the private key and move it out of *Downloads* into your SSH folder, not into a cloud-synced folder. On Windows the built-in OpenSSH refuses a key other accounts can read, so restrict it:

     ```powershell
     move $env:USERPROFILE\Downloads\ssh-key-*.key $env:USERPROFILE\.ssh\oracle.key
     icacls "$env:USERPROFILE\.ssh\oracle.key" /inheritance:r /grant:r "$env:USERNAME:R"
     ```

   - **Networking.** The wizard may create the instance with no public IP and a subnet with no route to the internet — then nothing works, not even the bot's outbound polling. Once the instance is *Running*, on its page:
     1. **Quick actions → Connect public subnet to internet → Connect.** Creates an internet gateway, a `0.0.0.0/0` route and a security group with the SSH rule.
     2. **Attached VNICs → *your VNIC* → IPv4 addresses →** menu on the private IP **→ Edit → Public IP type: Ephemeral public IP.** The address appears on the instance page in a few seconds.

     The subnet must show *Subnet access: Public* (**Networking → Virtual cloud networks → your VCN → Subnets**). A *Private* subnet cannot get a public IP and the flag cannot be changed afterwards — terminate the instance and recreate it in a public subnet. An ephemeral IP may change after a stop/start; the bot does not care (long polling makes no inbound connections), but your SSH config will. Reserve a public IP if you want it fixed — also free.
   - **Connect.** The login user depends on the image: `opc` on Oracle Linux, `ubuntu` on Ubuntu.

     ```bash
     ssh -i ~/.ssh/oracle.key opc@<PUBLIC_IP>
     ```

     Optional shortcut — add to `~/.ssh/config` and then `ssh oracle` is enough:

     ```t
     Host oracle
         HostName <PUBLIC_IP>
         User opc
         IdentityFile ~/.ssh/oracle.key
     ```

3. SSH in and install git + Docker.

   Ubuntu:

   ```bash
   sudo apt update && sudo apt install -y git docker.io docker-compose-v2
   sudo usermod -aG docker $USER && newgrp docker
   ```

   Oracle Linux 9 (no `apt`; Docker CE comes from Docker's own repository, the CentOS/RHEL one is the right one for Oracle Linux):

   ```bash
   sudo dnf install -y git nano dnf-plugins-core
   sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
   sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
   sudo systemctl enable --now docker
   sudo usermod -aG docker $USER && newgrp docker
   ```

   On the 1 GB **E2.1.Micro** add swap first, otherwise the image build can be killed for lack of memory:

   ```bash
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

   Verify with `docker run --rm hello-world`. Outbound traffic is open by default on both images; no firewall changes are needed.

4. Deploy (same on both images):

   ```bash
   git clone <your fork> track_weight_bot && cd track_weight_bot
   cp .env.example .env && nano .env            # paste your values
   mkdir -p secrets && nano secrets/service_account.json
   docker compose up -d --build
   docker compose logs -f                       # expect "sheet ready" and "starting as @..."
   ```

   The container runs as an unprivileged user (uid 10001), so the key file must be readable by it: `chmod 644 secrets/service_account.json` (a `600` key makes the bot exit with `PermissionError` at startup).

   `restart: unless-stopped` in `docker-compose.yml` brings the bot back after reboots. Long polling needs **no inbound ports** — leave the security list as the quick action created it (SSH only).

5. Set the VM timezone (`sudo timedatectl set-timezone Europe/Kyiv`, both images) or rely on `DEFAULT_TZ=Europe/Kyiv` in `.env`; the scheduler uses per-user `tz` from the `users` tab with `DEFAULT_TZ` as fallback.

Updating: `git pull && docker compose up -d --build`.

---

## Configuration reference (`.env`)

| var                                        | required | meaning                                                                                                                                           |
| ------------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`                       | yes      | from BotFather                                                                                                                                    |
| `ALLOWED_CHAT_IDS`                         | yes      | comma-separated group ids the bot serves                                                                                                          |
| `GOOGLE_SHEET_ID`                          | yes      | spreadsheet id                                                                                                                                    |
| `GOOGLE_SERVICE_ACCOUNT_FILE`              | one of   | path to JSON key, default `secrets/service_account.json` (docker-compose mounts `./secrets` and sets this to `/app/secrets/service_account.json`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON`              | one of   | full JSON content instead of a file                                                                                                               |
| `GEMINI_API_KEY`                           | yes      | from AI Studio                                                                                                                                    |
| `GEMINI_VISION_MODEL`                      | no       | default in `.env.example`                                                                                                                         |
| `GEMINI_TEXT_MODEL`                        | no       | default in `.env.example`                                                                                                                         |
| `DEFAULT_TZ`                               | no       | `Europe/Kyiv`                                                                                                                                     |
| `WEIGH_IN_DEADLINE`                        | no       | `11:00` — reminder time                                                                                                                           |
| `WEEKLY_REPORT_DAY` / `WEEKLY_REPORT_TIME` | no       | `sun` / `20:00`                                                                                                                                   |
| `WEIGHT_MIN` / `WEIGHT_MAX`                | no       | `40` / `200` — bare-number detection range                                                                                                        |
| `LOG_LEVEL`                                | no       | `INFO`                                                                                                                                            |

## How the bot decides what a message is

| message                                                                                                            | action                                                      |
| ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------- |
| photo (optionally with caption)                                                                                    | Gemini vision → kcal estimate → `food`                      |
| bare number in `[WEIGHT_MIN, WEIGHT_MAX]`, e.g. `84.3` / `84,3`                                                    | weight → `weight`                                           |
| reply to the bot's morning ping with a number                                                                      | weight                                                      |
| reply to the bot's food estimate with a number                                                                     | sets kcal of that estimate                                  |
| reply to the bot's food estimate with text (weight, ingredients, dish name)                                        | Gemini re-estimates it (with the photo) and updates the row |
| `/w`, `/food`, `/sport`, `/today`, `/week`, `/help` (+ transliterated and Cyrillic aliases, e.g. `/vaga`, `/вага`) | explicit commands                                           |
| text mentioning sport keywords (біг, зал, велосипед, плавання, …) or `/sport`                                      | Gemini text parse → `sport`                                 |
| anything else                                                                                                      | ignored                                                     |

You do not have to run `/start`: the first weight, food or sport message registers the sender in the `users` tab. Columns `tz`, `height_cm`, `target_kg`, `daily_kcal_target` and `active` there can be edited by hand and are preserved.

## Project layout

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Privacy

Photos are sent to Google's Gemini API and are not stored by the bot; only Telegram's `file_id` of the photo is kept in the `food` tab so a later text correction can show the photo to Gemini again (a `file_id` is usable by this bot only). Weights and food logs live in *your* spreadsheet, visible to whoever you share it with. Keep `.env` and `secrets/` out of git (they are in `.gitignore`).

## License

MIT — see `LICENSE`.

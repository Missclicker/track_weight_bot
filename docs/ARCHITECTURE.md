# Architecture

A single Python process, long-polling Telegram, storing everything in one Google Spreadsheet and
calling Gemini for the parts that need judgement (what is on the plate, what a sport sentence
means, how the week went) - and, optionally, Groq for the same calls while Gemini is out.

```
Telegram  <-- long polling -->  bot (aiogram)  --->  Google Sheets (gspread, thread pool)
                                    |
                                    +--->  Gemini API (google-genai, async client)
                                    |         |  overload / spent quota only
                                    |         +--->  Groq API (groq, async client; optional)
                                    |
                               APScheduler (morning ping, water reminders, weekly report)
```

## Module map

| module | responsibility |
|---|---|
| `bot/__main__.py` | Entry point. Loads settings (fails fast with a readable message), ensures the sheet schema, builds `Bot`/`Dispatcher`, injects dependencies, starts the scheduler and polling. |
| `bot/config.py` | `Settings` (pydantic-settings). Parses `ALLOWED_CHAT_IDS`, validates `HH:MM` times, weekday, timezone and that Google credentials exist; a blank `GOOGLE_SERVICE_ACCOUNT_JSON` or `GROQ_API_KEY` reads as unset. |
| `bot/i18n/` | Every user-facing string, in Ukrainian (`uk.py`) and English (`en.py`) - the same names, signatures and placeholders in both, which `tests/test_i18n.py` enforces. `t(lang)` picks the module for a reader; `normalize_lang` reads a `/lang` argument or a hand-typed `users.lang` cell; `majority_lang` decides a group's language. The package root holds only what no language owns: `FOOD_PREFIX`, `COMMANDS`, `mention`, `fmt_kg`, `fmt_delta` and the all-language prefix tuples routing recognises bot messages by (see *Languages*). Formatting helpers escape HTML. |
| `bot/parsing.py` | Pure functions: `parse_weight` (with `require_marker`, which demands the number carry a decimal, a "кг"/"kg" unit or a "вага"/"weight" label - the same regex groups, named, so the flag cannot drift from the pattern), `parse_correction`, `parse_kcal_target`, `parse_profile` (birth year or age, sex and height in any order, all-or-nothing, + the `ProfileUpdate` value object) / `parse_sex` (a typed word or a `users.sex` cell -> `"m"`/`"f"`), `is_delete_request` / `is_food_cancel_request` (the narrow and the wide cancel vocabulary), `strip_yesterday` (cuts the whole-word "вчора" out of a message and says it was there), `strip_meal_time` (takes a leading or trailing `H:MM` / `H-MM` off a `/їжа` text as the meal time), `ts_time` (the "HH:MM" of a stored `ts`), `parse_water_schedule` / `is_water_due` (+ the `WaterSchedule` value object). Every vocabulary is Ukrainian and English at once, whatever the sender's language (see *Languages*). |
| `bot/met.py` | MET table (activity -> MET, keyword regexes, typical pace, Ukrainian and English display name) and `kcal = MET * kg * h`; `activity_title(key, lang)` names an activity for a reader. |
| `bot/nutrition.py` | Pure numbers for the weekly report, None in -> None out: `age_on`, the age-based `protein_g_per_kg` and the `reference_weight` it multiplies, `bmi`, `bmr_mifflin` (Mifflin-St Jeor), `maintenance_kcal` (sedentary BMR + logged sport) and `energy_shares` (see *Nutrition numbers* below). |
| `bot/ai.py` | `GeminiClient`: `estimate_food` (photo or text), `revise_food` (text only), `parse_sport`, `weekly_report` - each takes a keyword-only `lang` for the language the model writes in (the only call with a role: `REPORT_SYSTEM_INSTRUCTIONS[lang]` goes out as the config's `system_instruction`, and a non-blank `previous_report` is appended after the data as a delimited "PREVIOUS REPORT" block - see *Weekly report* below). Pydantic response schemas with clamping validators; three attempts with escalating server deadlines and an optional one-shot "retrying" callback (see *Gemini retries* below); a per-model quota cooldown with `check_quota` / `QuotaExceeded` and the pure `quota_cooldown` parser (see *Gemini quota*); a separate per-model overload cooldown with `check_overload` / `ModelOverloaded`, which cuts the ladder short on a 503 (see *Gemini overload*). `_generate` wraps the Gemini ladder (`_generate_gemini`) and, when a `GroqClient` was passed in as `fallback`, hands the call to Groq on either outage; `check_available` is the photo precheck that knows about it (see *Groq fallback*). |
| `bot/fallback.py` | `GroqClient`: one Groq chat completion for a Gemini-shaped call - prompt strings and image `Part`s translated into chat content parts, the vision or text model picked by whether an image is present, a strict `json_schema` response format built from a pydantic schema by `strict_schema` plus a completeness check on the answer, an output cap on every request, reasoning kept out of the answer. Single attempt, no cooldowns (see *Groq fallback*). Not named `groq.py`, which would shadow the SDK. |
| `bot/sheets.py` | `SheetsRepo`: async facade over gspread (`asyncio.to_thread`), retry with backoff on 429/5xx, 60 s cache of the `users` tab, tab/header definitions; `set_lang` / `get_lang` for the person's language. |
| `bot/init_sheets.py` | `python -m bot.init_sheets` - idempotent schema creation, prints the sheet URL and row counts. |
| `bot/reports.py` | Aggregations: `day_food` (per-day food list/total for `/kcal` and the food replies; each entry is a `FoodItem(at, dish, kcal)`, `at` being the `ts` wall clock as "HH:MM"), `today_summary` and `build_weekly_payload` (the JSON given to Gemini: per person the week's totals and per-logged-day averages, energy shares, a `days` list, late meals, the `nutrition.py` numbers and a `previous_week` block, both weeks measured by the one `_window_numbers`; three `user_rows_between` reads per person, split into the two windows in memory - see *Nutrition numbers*); two pure helpers for the report text: `previous_advice` (last week's stored report, in either language, minus its header and the prompt's `<<<`/`>>>` markers, or None when it carries no advice) and `split_message` (Telegram-sized pieces of at most 4000 characters). |
| `bot/scheduler.py` | `Jobs` (ping per timezone, water tick, weekly report, `chat_lang` - the language of a message to a whole chat) and `build_scheduler`. |
| `bot/handlers/` | aiogram routers, one file per feature; `__init__.py` assembles them and holds the allowed-chat gate, the `SenderLang` middleware and the global error handler. |

## Data flow

**Message routing.** The root router has two children, tried in order. `guarded` carries the
`AllowedChat` filter and drops every message whose `chat.id` is not in `ALLOWED_CHAT_IDS` (a
private chat whose id is listed - e.g. the owner testing in a DM - is served like a group).
`private` then serves the three commands that make sense outside the group: `/start` (a member is
told the bot can now DM them, anybody else gets "works only in the group"), `/вода` and `/мова`
(`/lang`, members only, like `/вода`).
Inside `guarded` the routers are tried in order:

1. `commands` - `/start /help /w /food /sport /today /kcal /target /profile /week /lang`. `/target`
   writes only the `daily_kcal_target` cell (`set_daily_kcal_target`), so other hand-edited
   columns are untouched; the target is optional and the i18n `_target_suffix` hides it when it is
   missing, zero or not a finite number. `/profile` (`/профіль 1981 ч 180`) stores the optional
   birth year, sex and height the weekly report can use: it merges what the message names into
   the sender's current values and `set_profile` writes only those three cells, but on *every*
   `users` row of the user - they describe the person, not their membership of one chat, and a
   DM must not disagree with the group about somebody's age. A message `parse_profile` cannot
   read in full is answered with the usage text and stores nothing. `/food` and food photos
   share `photos.record_food`, which reads the sender's food rows for today so the `≈` reply
   ends with "Разом за сьогодні: N ккал" (the new entry included); `/kcal` lists those rows, each
   line starting with the time it was logged at, or the meal time stated with it ("07:54 - 390
   ккал - ..."), read straight off the row's `ts` - it is already in the user's timezone, so no conversion happens. A hand-edited row
   whose `ts` carries no usable time shows `KCAL_NO_TIME` ("--:--") instead, never midnight.
   `/kcal вчора` (`strip_yesterday` on the argument) lists the previous day the same way under
   its own header; any other argument is ignored.
   A bare `/food` or `/sport` (tapped from Telegram's command menu) answers with a `ForceReply`
   prompt and the reply to it is recorded - the `InputPrompt` filter recognises the prompt by its
   text prefix, so it survives a restart. Those two handlers are registered in `commands`, the
   first router inside `guarded`, on purpose: a bare number answering the food prompt must be read
   as food rather than grabbed by `weight`. A delete word answering either prompt (`PromptCancel`,
   registered before them) cancels the command instead: no Gemini call, no row.
2. `water` - `/вода` (`/water`, `/voda`): show, set or cancel the sender's water reminders.
3. `corrections` - a reply to a bot message that starts with `≈` (the food-estimate prefix).
   A number -> `update_food_kcal(user_id, message_id)`. A cancel phrase
   (`parsing.is_food_cancel_request`)
   -> `delete_food_entry`, registered first so "видали" is never shipped to Gemini as a correction.
   That predicate is the *wide* vocabulary: the delete verbs plus filler words that
   `parsing.is_delete_request` already took ("видали цей запис"), plus the regret phrases people
   type instead of an order - "не записуй", "це жарт", "я випадково", "помилково" (and "don't log",
   "just kidding", "by accident", "my mistake"). Both are whole-message and whole-word, so "прибери
   хліб", "не записуй хліб" and "remove the bread" - drop an ingredient from the estimate - stay
   corrections of the dish, "помилкова порція" is not "помилка", and "remove one" is one piece
   fewer, not a delete ("one" is no filler word; "delete this one" is a whole phrase). The regret
   half is deliberately food-only (`sport.SportDeleteReply`, `commands.PromptCancel` and
   `commands.InputPrompt` keep asking `is_delete_request`): a wrong photo is the thing people
   regret out loud, and widening the vocabulary everywhere would start eating ordinary replies.
   A reply that `weight.weigh_in` claims as a weigh-in is refused by all three kinds, which is what
   keeps this router and `weight` mutually exclusive. Any other text -> `get_food_entry`,
   `GeminiClient.revise_food` with the earlier estimate + the user's text - text model only, the
   photo is never re-sent -> new `≈` reply -> `update_food_entry`, which also re-keys
   the row to the new reply's `message_id` so corrections can be chained. All three are scoped to
   the sender, so only the author of an entry can correct or delete it and equal `message_id`s from
   different groups never collide. All three replies end with the total for the day the entry
   belongs to ("за сьогодні" or "за <date>"), read back from the sheet after the write.
4. `weight` - a number in `[WEIGHT_MIN, WEIGHT_MAX]` that is not a reply (`source="text"`), or one
   replying to any message of *ours* -> `add_weight`. `weight.weigh_in` is the single decision: the
   morning ping gives `source="ping"`, every other bot message (a sport confirmation, `/kcal`, the
   weekly report, an error reply) gives `source="reply"`, because people do weigh in by answering
   whatever is on screen. Our messages are told apart by their text prefix, never by a remembered
   message id, so the whole thing survives a restart - and the prefix of every language counts
   (`i18n.PING_PREFIXES`), since a ping sent in one language is answered by people reading another. A reply to another *person* is conversation
   and is ignored. The one stricter case is a reply to the `≈` food estimate, where
   `parse_weight(require_marker=True)` demands a decimal, a "кг"/"kg" unit or a "вага"/"weight"
   label: 40..200 overlaps perfectly plausible kcal corrections of a portion, so a bare "84" under
   an estimate stays a correction and only "84.3" / "84 кг" / "вага 84" is a weigh-in. Replies to
   the `/їжа` and `/спорт` prompts never reach here - `commands` is the first router inside
   `guarded` and records them as food or sport.
5. `photos` - any photo -> Gemini vision -> reply -> `add_food` with the *reply's* `message_id`
   so a later correction can find the row.
6. `sport` - a delete word (`parsing.is_delete_request`, the narrow vocabulary - the regret phrases
   delete food rows only) in reply to a bot message starting with `Спорт:` or `Sport:`
   (`i18n.SPORT_PREFIXES`) -> `delete_sport_entry`.
   That is the whole router: any other reply to a sport confirmation is ignored, re-estimating an
   activity is not a thing the bot does - except a number, which `weight` (tried before this
   router) has already taken as a weigh-in.

Recording sport has no router of its own: free text is never scanned for sport keywords (too many
false positives in a chatty group), so `sport.record_sport` is reached only from `/sport` and its
prompt reply -> Gemini text parse -> kcal from the MET table -> reply -> `add_sport` with the
reply's `message_id`, the same ordering `photos.record_food` uses.

**Deletes are hard deletes** (`worksheet.delete_rows`), not a `deleted` flag: every aggregation
(`day_food`, `today_summary`, `build_weekly_payload`, `user_rows_between`) then stays correct
without learning about a flag. `delete_food_entry` returns the row it removed so the handler can
total that entry's day again without a second scan of the tab.

Filters return a `dict` on match (`{"kg": 84.3, "source": "text"}`), which aiogram injects into
the handler - so parsing happens once and unmatched messages fall through to the next router.
Anything unmatched is ignored.

**Users.** Nobody has to run `/start`: the first weight/food/sport message registers the sender
(`ensure_user`). The `users` tab is editable by hand - `tz`, `height_cm`, `target_kg`,
`daily_kcal_target`, `birth_year`, `sex`, `lang`, `active` are preserved on upsert. `birth_year`,
`sex` and `lang` are trailing columns (see *Trailing columns*); a hand-typed cell counts only when
it makes sense - a whole year in 1900..2100, a sex word `parse_sex` knows, a language
`i18n.normalize_lang` knows ("en", "English", "укр") - and is otherwise read as unknown. A blank
`birth_year`, `sex`, `height_cm` or `lang` cell on one row is filled on read from the person's other
rows (`_all_users_sync`; the row's own value wins), so every row of a person answers with the same
profile and language, and `/profile 45` from the DM merges the group's values, not blanks.

**Languages.** Ukrainian and English. The language belongs to the person: `/lang en` (`/мова`,
`/mova`, `/language`; `/lang` alone shows the current one) runs `set_lang`, which writes the `lang`
cell on *every* `users` row of the sender, like `/profile`, and clears the users cache so the very
next message is answered in it. An unset cell means Ukrainian, so everybody who registered before
English existed sees no change. A reply to one person speaks that person's language: `SenderLang`,
an *inner* middleware on the root router's message observer (aiogram runs a router's inner
middlewares for every nested router, and only after a handler's filters matched, so ignored chatter
costs no lookup), injects `lang` from `get_lang` - the cached `users` tab - and handlers take
`i18n.t(lang)`. The error observer does not see handler data, so `on_error` looks the language up
again. Both lookups fall back to Ukrainian with a WARNING instead of raising: a Sheets outage must
not turn `/help` into an error, nor swallow the error reply it is reporting. A correction or delete
is answered in its sender's language, who is always the entry's author.
What the model writes for a person follows them too: `estimate_food`, `revise_food` and
`parse_sport` take `lang`, the food prompts ask for the dish, portion and notes in that language
(the sheet's `dish` column therefore holds both), and the sport title comes from
`met.activity_title`. A message to a whole chat - the morning ping and the weekly report with its
fallbacks - is written in `Jobs.chat_lang`: a DM in its owner's language, a group in the language
most of its *active* users chose (`i18n.majority_lang`: an unset language votes Ukrainian, a tie
goes to English), and a group with nobody active keeps the default - no votes is not a tie. The
ping votes over the whole active roster it already reads, not only the people it mentions. A water
reminder is a DM and uses its subscriber's language. A failed language read never costs a ping, a
reminder or a report: it falls back to Ukrainian with a WARNING. Recognising our own messages works
in every language at once (`i18n.SPORT_PREFIXES`, `PING_PREFIXES` and the two input-prompt
tuples), because a message stays on screen after its reader switches; the `≈` food prefix is
shared. Input is never tied to the setting: every parser understands both vocabularies for
everyone. The only Ukrainian in the English strings is the way back ("перейти на українську:
/lang uk" in `/help` and `/lang`), as the Ukrainian `/lang` carries the English way out - somebody
who switched by mistake may not read the language they landed in.

**Dates.** Every timestamp is written in the user's timezone (`users.tz`, fallback
`DEFAULT_TZ`) and "today" is computed there. The `date` column is the lookup key for all
aggregations.

"вчора" ("учора", "yesterday") in `/їжа`, `/спорт`, a reply to either prompt or a photo caption
files the entry under the previous day in the user's timezone: `date` is that day, while `ts`
stays the moment the message was sent (unless the meal's time is stated, see below), so the two
columns disagree on purpose. `strip_yesterday` removes the marker before the text reaches
Gemini - the dish name and the activity title come back from the model, and "вчора млинці" would
otherwise be stored as the dish. The confirmation names the day it was filed under ("Разом за
2026-09-18: ...", "Записано за 2026-09-18."), and `/їжа вчора` with nothing else is still the bare
command: the bot asks for the description - as does a prompt reply that is only "вчора" or a time.

A `/їжа` text (the command's arguments or a reply to its prompt - not a photo caption, not
`/спорт`) may also state when the meal was eaten: a clock `H:MM` / `HH:MM` with `:` or `-` as the
*first or last* whitespace-separated token ("14:00 борщ", "яєчня з 3 яєць 14-00"), a trailing comma,
period or semicolon allowed. Then `ts` is that clock on the day the meal counts towards (today, or
yesterday with "вчора") with the offset the user's zone has on that date, and `date` is unchanged -
so a timed row's two columns agree again, `/kcal` shows the stated time and `user_rows_between`,
which sorts by `ts`, puts a backfilled meal in its place. `strip_meal_time` removes the token
before Gemini sees the text and runs *before* `strip_yesterday`, so the position is judged on the
text as typed: "14-00 вчора кава" and "вчора кавун 14:00" carry a time, "вчора 14:00 кава" does not.
The rule is narrow on purpose - a time in the middle is usually part of the description, and
anything else (`14.00`, `14 00`, `14год`, `о 14:00`, an invalid `25:00`) stays in the text
untouched. Only one time is taken: with clocks at both ends the first one wins and the last goes to
Gemini. A time later than now is accepted as stated. `/їжа 14:00` alone is the bare command, and
the time is not carried over to the reply - the reply may state it again.

**Scheduler.** At startup and nightly at 00:05 the bot collects the distinct timezones of active
users and (re)creates one cron job per timezone at `WEIGH_IN_DEADLINE`. The job mentions
(`<a href="tg://user?id=...">`) everyone in that timezone without a `weight` row for today. The weekly job runs on `WEEKLY_REPORT_DAY` at
`WEEKLY_REPORT_TIME` in `DEFAULT_TZ` (default Monday 09:00): build payload -> Gemini -> send ->
`reports` tab. If Gemini fails, the numeric summary is sent instead. The window is always the 7
*full* days before the run (`today - 7 .. today - 1`), so the current, half-logged day never
skews the numbers. Ping and report are written in the chat's language (see *Languages*). A chat
with a positive id is a DM: it gets `PERSONAL_REPORT_PROMPTS` instead of the group one, and the scheduled run skips it entirely when that user is also an active member of
one of the allowed group chats (they already get their numbers there). `/week` ignores that skip -
an explicit ask is always answered.

**Weekly report.** The report is written by a nutritionist persona - evidence-based, with
sports-nutrition and 40+ expertise, warm and never shaming, no diagnoses or doses, a doctor only
for the red flags it names, only the numbers in the data and never an invented one (concrete
targets in the recommendations and differences between given numbers are fine), plain text in
the chat's language. That role is
`REPORT_SYSTEM_INSTRUCTIONS[lang]`, sent as the config's `system_instruction` rather than as a paragraph
of the prompt: the persona and its rules hold for the whole answer whatever the week looked like,
so they stay apart from the per-week task and the data, and the free text inside the prompt
(names, last week's report) has a harder time overriding them. The other three Gemini calls send
no system instruction. The two prompts carry the rest: the labelled lines per person, the length
(~1100 characters a person in a group, ~2500 in a DM), the simpler report for somebody without a
profile, how the bot computes the protein target (so the model explains it but never recomputes
it) and notes on what the payload fields mean.
Every language-dependent piece of the report prompts - the language named, the seven line labels
(Енергія ... На цей тиждень / Energy ... This week), the `/профіль 1981 ч 180` / `/profile 1981 m
180` hint - is one `_ReportWords` entry per language, and the prompts are built from it once at
import into the `REPORT_SYSTEM_INSTRUCTIONS`, `REPORT_PROMPTS`, `PERSONAL_REPORT_PROMPTS` and
`_PREVIOUS_REPORT_BLOCKS` dicts; the Ukrainian ones are byte-identical to the single-language
prompts they replaced (a test pins that), and the old `REPORT_*` names still point at them.
For continuity the job hands the model last week's report:
`SheetsRepo.get_previous_report(chat_id, week_start)` picks the stored report of that chat whose
`week_start` falls in `[week_start - 13, week_start - 7]`, the latest `ts` winning. That is a window which ended before
this one began, at most a week earlier: a mid-week `/week` overlaps the current window, so its
numbers are partly this week's and it is not "last week", and anything older is too stale to
follow up on. `reports.previous_advice` then drops the header line and turns a numbers-only
fallback or a no-data text into None - neither holds advice, and handing one over would invite a
follow-up on advice nobody gave. It also removes every `<<<` and `>>>` (the cell is editable by
hand, and a stray `>>>` would close the block below early) and caps the text at 8000 characters,
far above any stored report, only to bound a hand-edited cell. The text goes after the data in a
`<<< >>>` block the model is told to treat as data and to check against the numbers (whether its
advice was followed) rather than repeat; that check is asked for only there, so without a previous
report the prompt does not mention one at all. The lookup is optional context, so its failure is
caught, logged as a WARNING, and the report is written without it.
A group report can outgrow a Telegram message (4096 UTF-16 units), so every text - AI,
numbers-only or no-data - goes through `reports.split_message`: pieces of at most 4000 characters,
cut at the last paragraph break that fits first (a person's block stays whole: only a break right
under the first line is passed over, since it would send the header alone), then at line breaks,
then hard, all sent in order with `parse_mode=None`; only the first carries the header. It is
still one report: the full text is stored once, in a single `reports` row, which is exactly what
next week's lookup reads back. It is stored as soon as one piece went out, even if a later send
fails - the chat has read part of it - and the send error is then re-raised so `weekly_reports`
logs the chat; when the first send fails nobody saw it, and nothing is stored.

**Nutrition numbers.** Every number the report talks about is computed by the bot -
`reports.build_weekly_payload` with the pure `bot/nutrition.py` - for the reason sport kcal come
from the MET table: the model interprets numbers, it never computes or invents one. A figure it
derived itself (a g/kg of a weight it picked, an average over 7 days instead of the logged ones)
could be wrong and would disagree with what the bot shows next week, so the prompts only name the
keys, and a value that cannot be computed is null, never a guess. Per person the payload adds the
protein, fat and carbs averages per *logged* day (like `kcal_avg_per_day`), `energy_share_pct`
(4 / 9 / 4 kcal per g plus the alcohol kcal; the macro energies, not `kcal_total`, are the
denominator, because the model's kcal and macros do not always agree and the shares should add up
to ~100), a `days` list (kcal, protein, alcohol and entries per logged day - the day counts against
a target are taken from these rounded figures, so the two never disagree), `alcohol_days`,
`meals_per_logged_day` and `days_over_kcal_target` (null without a target that passes the
i18n `_target_suffix` rule). The protein target is `protein_g_per_kg(age)` x `reference_weight`:
1.2 g/kg below 40, 1.5 g/kg from 40. The 0.8 g/kg RDA is for weight-stable adults; a calorie
deficit raises the need to keep lean mass, and muscle responds less to protein with age (anabolic
resistance), which is why guidance for older adults sits at 1.0-1.2 g/kg and higher with training
or a deficit - the group chose 1.5 from 40. The kilograms are the target weight when it is set and
below the current weight, otherwise the current weight: g/kg of a body weight carrying a lot of fat
overshoots, and the target weight is the practical proxy. `weight_current` is the last weigh-in on
or before `week_end`, however old, so somebody who skipped the scale this week still gets a target;
its date travels along as `weight_current_date` so the model does not present an old weigh-in as
this week's, and a row whose `kg` is blank, zero or unparseable or whose `date` is not ISO (a hand
edit) is passed over for the one before it instead of wiping every number sized on the weight.
From it come `bmi`, `bmr_kcal` (Mifflin-St Jeor, which also needs height, age and sex) and
`maintenance_kcal_est` = 1.2 x BMR + this week's sport kcal / 7 - the *sedentary* factor, because
the logged sport is added on top and an activity level would count it twice. It is rough, easily
±15-20 % (a population formula plus MET-table sport), and the prompt says so. `late_meals` counts
entries whose `ts` is at or after 21:00 *and* on the entry's own `date`: a row backdated with
"вчора" keeps the moment it was sent in `ts`, so its clock time says nothing about when the meal was
eaten - unless the meal's time was stated ("/їжа вчора шаурма 22:00"), which puts `ts` on that
`date` and makes its clock count. `previous_week` holds the 7 days before
(`previous_week_start..previous_week_end`, both at the top level): days logged, the kcal and protein averages, g/kg, alcohol, vegetables, sport, the
last weight and its delta, computed by the same `_window_numbers` as the current week so the two
definitions cannot drift apart - except that its g/kg divides by *this* week's reference weight, so
the two compare; it is null when that window has no rows. The extra window costs no extra read:
every `user_rows_between` scans a whole tab against the Sheets per-minute read quota, so food and
sport are read once over `previous_week_start..week_end`, weight once from `date.min`, and the
rows are split into the windows by their `date` in memory - three reads per person, as before. Who
appears is unchanged (rows in the current window). A person without a birth year, sex or height
simply gets nulls - no age means no protein target, no BMR and no maintenance estimate; no sex or
height, no BMR; no height, no BMI - and the simpler report with one hint at `/profile`. The numbers-only fallback
(`weekly_stats_block` in `bot/i18n/`) shows the protein average and, when there is one, the target.

**Water reminders.** One `water_tick` job runs every minute and walks an in-memory list of the
active rows of the `water` tab, so a per-user interval costs neither a job per subscriber nor a
Sheets read per minute. Each subscription carries its own zone key (copied from `users.tz` when it
was created), the tick converts the current minute into that zone and `is_water_due` decides
whether this minute is on the schedule's grid; the ping is a private message. The list is reloaded
together with the ping jobs (startup, nightly 00:05) and by the `/вода` handler, so hand edits of
the tab are picked up. `TelegramForbiddenError` (blocked bot, deleted chat) deactivates the row and
drops it from memory; any other send error is logged and the remaining subscribers still get theirs.
Telegram refuses a DM to a user who never pressed Start, so the handler sends the confirmation
message first and stores the subscription only when it went through.

**Gemini retries.** `GeminiClient._generate_gemini` makes up to three attempts
(`_ATTEMPT_TIMEOUTS_S`) with backoff sleeps of 1.5 s and 3 s, the same shape as `sheets.with_retry`. The deadline
*escalates* per attempt - 45 s, 75 s, 120 s - because the SDK turns `http_options.timeout` into an
`X-Server-Timeout` header: Google's backend enforces our own deadline and answers
`504 DEADLINE_EXCEEDED` when the model needs longer, so retrying with the same 45 s during busy
hours can never succeed. Each attempt therefore passes its own `types.HttpOptions` on the
`GenerateContentConfig`; per-request options win over the client-level ones in the SDK and drive
both the transport timeout and that header. The outer `asyncio.wait_for` keeps a `_TIMEOUT_GRACE_S`
margin over the attempt's deadline so it stays a backstop and the SDK's informative error surfaces
first. Retried: transient API codes (408/5xx), an empty response body, and aiohttp/httpx
transport failures (both arrive transitively, so the imports are guarded); 400/403/404 break out
after one attempt - a bad key or a retired model will not fix itself.

**Gemini quota.** A 429 is *not* transient: it means the model's free-tier budget is spent, so
`_generate_gemini` puts that **model** into a cooldown (`GeminiClient._cooldowns`) and raises
`QuotaExceeded` at once, without a second attempt and without the "retrying" notice - another
request would only burn another unit of the same counter. The cooldown is per model on purpose:
the vision model's requests-per-day runs out long before the text one's, and `/їжа <текст>`,
`/спорт`, corrections and the weekly report must keep working when photos no longer do. Its
length comes from the pure `quota_cooldown(details)`, which reads the 429 body: a
`google.rpc.QuotaFailure` whose `quotaId`/`quotaMetric` mentions "per day" means waiting for the
next midnight in `America/Los_Angeles` (where Google resets the daily counters), anything else
uses the `google.rpc.RetryInfo` `retryDelay`, floored at 30 s (a 1 s delay would make the cooldown
pointless) and defaulted to 60 s. Not every 429 carries either part, so the parser never raises
and falls through to that default. The daily wait is measured in *elapsed* seconds (both ends go
through UTC before the subtraction, because CPython ignores a shared `tzinfo` and would otherwise
count wall-clock hours across a DST switch) and capped at 24 h, which errs the safe way. The
register is in memory only: a restart forgets it and the next 429 simply re-arms it. `check_quota` is called at the top of every attempt (a concurrent call
may have armed the cooldown while this one slept) and, ahead of everything else, by
`photos.on_photo` through `check_available` - a photo download and a Sheets read are not worth
paying for just to learn the vision quota is gone (unless the Groq fallback is on, which can still
answer). Unless Groq answers instead (see *Groq fallback*), the user gets one of four
messages (`quota_notice`, in the sender's language, chosen by *which* model ran out and *whether* it was a per-day
limit); the photo ones point at `/їжа <текст>`, which still works - unless the text model is unusable too, in which case
`_is_vision_only_outage` drops that advice: either both env vars name the same model, or the text
model is itself out of quota or riding out a demand spike (see *Gemini overload*).
`scheduler.run_weekly_report` needs no change: it already catches
`Exception` around the Gemini call and degrades to the numbers-only fallback.

**Gemini overload.** A 503 ("this model is currently experiencing high demand") *is* transient, so
it stays in `_TRANSIENT_CODES` and the first retry still happens - but the ladder stops there
(`_OVERLOAD_MAX_ATTEMPTS`, 2, clamped to the ladder's own length so shortening `_ATTEMPT_TIMEOUTS_S`
cannot put the budget out of reach and switch this whole path off). The escalation exists to give a
slow backend a longer deadline, and an overloaded model refuses immediately instead of running long,
so attempt three could only repeat the refusal - at the price of ~28 s of somebody staring at a
photo they just sent. The "retrying" notice is skipped too: the whole call is over in about two
seconds, and `AI_RETRYING` followed straight away by the overload reply is two messages for
nothing (a notice already sent because of an earlier non-503 failure in the same call stays).
Detection is `APIError.code == 503` only, never the response text: the wording and its locale are
not a contract. When it gives up, `_generate_gemini` sets the model aside for `_OVERLOAD_COOLDOWN_S` (30 s
- long enough that the next photo in the same spike does not pay the retry budget again, short
enough that a spike which clears in seconds is forgiven inside the same conversation), logs its own
WARNING naming the cooldown - checked ahead of the per-attempt log line, so a give-up reads as one
event rather than a bare "attempt 2 failed" the reader has to join up - and raises
`ModelOverloaded`. The register (`GeminiClient._overloads`) is deliberately *separate* from
`_cooldowns`: a spent quota and a busy model are different conditions with different replies, and a
quota can be gone for the rest of the day. Like `_cooldowns` it lives in memory only - 30 s of state
is not worth persisting across a restart. `check_overload` mirrors `check_quota` exactly: at the top
of every attempt, and (through `check_available`) ahead of the download in `photos.on_photo`.
When Groq is off or has failed too, the reply is `busy_notice`
(two messages, chosen by which model is busy), and its photo variant offers `/їжа <текст>` only when
the text model is actually usable right now - not overloaded and not out of quota
(`_is_vision_only_outage`, which the quota path shares for the same reason), because advice that
cannot work is worse than none. `scheduler.run_weekly_report` again needs nothing: `ModelOverloaded`
is an `Exception` and lands in the same numbers-only fallback.

**Groq fallback.** With `GROQ_API_KEY` set, `bot.__main__` builds a `GroqClient` and passes it to
`GeminiClient` as `fallback`; without one the bot behaves exactly as before. `_generate` is a thin
wrapper around the Gemini ladder (`_generate_gemini`, unchanged): when that raises
`QuotaExceeded` or `ModelOverloaded` - and only those two - the same request goes to Groq once.
Those outages say Gemini cannot answer *right now*, not that the request is wrong, so another
provider may well answer it; a 400/403/404, an exhausted 504 or transport ladder or an empty body
after three attempts is either our fault or a slow backend, and propagates as before without a
Groq call. Because `check_quota` / `check_overload` run at the top of Gemini's attempt loop, a call
made while a model is cooling down raises on attempt 0 and goes straight to Groq without spending
a Gemini request - which is the point of the cooldown. `GroqClient.generate` takes the very
`contents` list Gemini would have got and translates it into one user message: a prompt string or
a text `Part` becomes a text part, an image `Part` becomes an `image_url` part carrying a
`data:<mime>;base64,...` URL, in the original order (a text-only call sends a plain string, which
every chat model accepts); anything else raises and counts as a failure rather than quietly
dropping part of the request. A system instruction becomes a leading system message. An image
picks `GROQ_VISION_MODEL`, otherwise `GROQ_TEXT_MODEL` - decided from the contents, not from which
Gemini model gave up, since both Gemini env vars may name the same model. A schema becomes a
`json_schema` response format with `strict: true`. The first version was best-effort
(`strict: false`) so the pydantic defaults could cover a missing number, and production showed what
that buys: a photo estimate from `qwen/qwen3.8-27b` came back with its key quoting broken
(`"protein_g": 25, "fat_g\": 18, ": 50, ...` - the `\"` escapes the quote that should close the
key). Sometimes that is still valid JSON with junk keys like `fat_g": 18, `, which pydantic
ignores before filling every real field with its default, so the meal was logged as 25 g of
protein and zeros; sometimes the model loops until the token limit and Groq answers 400
`json_validate_failed`. Strict mode constrains the decoding to the schema instead, and the same
request answered completely in 130 tokens. It only accepts a schema in which every object lists
all its properties in `required` and sets `additionalProperties: false`, so `strict_schema` builds
one from `model_json_schema()` (on a deep copy, so the dict it was handed is never changed): those two rules on
every object in the tree, `$defs` included, so a nested model added later cannot go out
non-strict; `default` and `title` dropped everywhere, as is the model's top-level `description`;
field descriptions, bounds and the `anyOf: [..., {"type": "null"}]` of an optional field kept (a
nullable field is required too, the model answers `null`). A free-form mapping field has no key set
to require and raises rather than going out as an object that must stay empty. Behind strict mode,
for an operator-configured model that ignores it, a schema answer must parse as a JSON object
carrying every top-level property of the schema; otherwise `ValueError` names the missing keys and
quotes the first 300 characters of the answer, which the WARNING below shows. Every request also
carries `max_completion_tokens`, because Groq's free tier counts the *requested* cap against a
model's output tokens per minute, and with no cap the server reserves the model's maximum: the free
vision model's limit is 1000 OTPM, and an uncapped photo estimate was refused outright with 429
"Request too large ... Requested 2041". A schema call with an image goes to that vision model and
gets 600, chosen to stay under its 1000; Qwen's hidden reasoning counts toward it too, so a photo
fallback failing with `json_validate_failed` or `length` would point at this cap. A schema call
without one (text food estimate, revise, sport) goes to the text model and gets 2048: GPT-OSS's
reasoning counts toward the cap even when it is kept out of the response, and at 600 a text call
ran out before the answer - the server refused with 400 `json_validate_failed`, "max completion
tokens reached before generating a valid document". The text model is not under the vision model's
1000 OTPM limit, and the weekly report already asks it for twice as much. The free-text weekly
report gets 4096, since a group report runs to several thousand characters of Ukrainian, and if a
model refuses that cap the report job already degrades to its numbers-only fallback. An answer
whose `finish_reason` is `length` was cut off at the cap and raises, for both kinds of call.
Reasoning models are told to keep their thinking out of `message.content` (`include_reasoning:
false` for GPT-OSS, which rejects `reasoning_format`; `reasoning_format: "hidden"` for Qwen and
MiniMax models, which inline a `<think>` block by default and require `parsed` or `hidden` in JSON
mode). GPT-OSS also gets `reasoning_effort: "low"` on every call, the weekly report included: the
structured answers need no deep thinking, a report written from precomputed numbers needs little
more, and the hidden reasoning still spends the output cap and the user's wait. Qwen takes
different values for that field on Groq and MiniMax is left at its default as well, so neither
carries it. Any other model gets none of these fields, and a leading `<think>...</think>` is
stripped as a safety net. An empty answer raises. For a schema call the wrapper validates the
answer with the schema inside the fallback attempt, so a malformed Groq answer counts as a failed
fallback and ends in the outage reply instead of surfacing later as a generic error from
`estimate_food`. Groq gets a single attempt (`max_retries=0` on the SDK, one 60 s deadline) and no
cooldown register: the user has already waited for Gemini to give up, and the fallback exists to
shorten that wait, not to add a ladder of its own. `on_retry` is never passed on for the same
reason. The hand-over is one WARNING naming the Gemini model, the outage and the Groq model; when
Groq fails too, a second WARNING carries its error (no traceback - a second provider being down
during the first one's outage is just as expected) and the *original* outage is re-raised
untouched, `__cause__` included, so `on_error` words the busy/quota reply exactly as without a
fallback. The answer itself is silent about where it came from: the public methods parse Groq's
text the same way as Gemini's, and no i18n string mentions Groq. The photo precheck in
`photos.on_photo` goes through `GeminiClient.check_available`, which runs both cooldown checks only
when no fallback is configured: with Groq available a cooling-down vision model is exactly the case
worth downloading the photo for. The weekly report benefits without any scheduler change.

**The retry notice.** The four public methods take an optional `on_retry` callback that fires
*once* per call, just before the first backoff sleep - or before the second one, when the first
failure was a 503 and the overload path suppressed it. The interactive call sites
(`photos.on_photo`,
`commands._record_food_text`, `corrections.on_text_correction`, `sport.record_sport`) pass
`partial(message.reply, i18n.t(lang).AI_RETRYING)`, so somebody waiting on a slow estimate is told the
answer is late instead of staring at silence; the notice is left in the chat. Its wording names no
cause, because the same notice covers a deadline, a 5xx and a dropped connection. A send that fails
is
logged and the retry continues. `scheduler.run_weekly_report` passes no callback: nobody waits on
a background job and it already degrades to the numbers-only fallback.

**Errors.** A global error handler logs the exception and replies with a short "не вийшло,
спробуй ще" (it only fires when a handler matched, so the sender was always waiting). Two
exceptions from `bot/ai.py` are special-cased: `QuotaExceeded` gets `quota_notice` and
`ModelOverloaded` gets `busy_notice` (in the sender's language, see *Languages*), each with a WARNING-level log line naming the model and
the cooldown instead of a traceback - a spent free-tier quota and a demand spike are expected,
self-healing conditions, not bugs to hunt. With the Groq fallback on, either one only reaches the
handler once Groq has failed too, so the handler needs no knowledge of Groq. The polling loop never dies
because of a handler.

**Trailing columns.** `food.portion` (the portion size the model priced, shown on the `≈` line so
the user can see what the calories were computed for), `sport.message_id` (the confirmation a
delete replies to), `users.birth_year` / `users.sex` (the profile for the weekly report) and
`users.lang` (the person's language) are
the *last* entries of their `HEADERS` lists: an existing spreadsheet then only gains a trailing
column instead of having every value shifted right.

**Sheets writes.** Everything is appended with `value_input_option=RAW`: names and dishes can
never be evaluated as formulas, and the ISO `date`/`ts` strings we filter on are not re-formatted
by the spreadsheet locale. Registration of a new user is serialised with an `asyncio.Lock`
because aiogram handles each update in its own task.

## Decisions

- **Long polling, not webhooks.** No public endpoint, no TLS, works from a home box or a
  free VM behind NAT. Only one instance may poll at a time.
- **Google Sheets as the database.** The group wants to look at and edit the data; a sheet is the
  UI. Reads scan whole tabs (`get_all_records`), which is fine for a handful of people for years;
  if it ever hurts, cache tabs the same way `users` is cached.
- **Gemini Flash on the free tier.** Vision for photos, text model for sport parsing and the
  report. Structured output (`response_schema`) keeps parsing deterministic; numbers are clamped
  rather than rejected so one odd field does not lose a meal.
- **Kcal for sport is computed locally** from the MET table using the user's last known weight,
  so the AI only has to extract *what* and *how long*.
- **Everything blocking runs off the event loop.** gspread via `asyncio.to_thread`, Gemini via
  `client.aio`, Groq via `AsyncGroq`.
- **No emojis** in bot output; plain Ukrainian or English text with HTML only for mentions and `<code>`.

## How to add a feature

1. Put the parsing/decision logic in a pure function (`parsing.py`, `met.py`, `reports.py`) and
   write a test in `tests/` first - `tests/conftest.py` has an in-memory `FakeRepo`.
2. Add the user-facing strings to both `bot/i18n/uk.py` and `bot/i18n/en.py` (same name, same
   placeholders - `tests/test_i18n.py` fails otherwise) and reach them through `i18n.t(lang)`.
3. If new data is stored, add a column at the end of the relevant tab in `sheets.HEADERS`, a
   repo method, the same method on `FakeRepo`, and update the README table.
4. Add a handler module under `bot/handlers/` exposing `build() -> Router`, and include it in
   `handlers.build_router()` at the right position in the order above.
5. Run `ruff check .` and `pytest`; `tests/test_routing.py` shows how to drive a handler through
   a real `Dispatcher` with a mocked Telegram session.

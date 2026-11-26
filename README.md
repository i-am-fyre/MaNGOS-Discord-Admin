<p align="center">
  <a href="https://github.com/i-am-fyre/MaNGOS-Discord-Admin" target="_blank">
    <img height="150" alt="default bot banner" src="/img/default-bot-banner.svg"/>
  </a>
</p>
<p align="center">
  <a href="https://discord.gg/CzXcBXq">
    <img src="https://img.shields.io/discord/286167585270005763?label=Discord&logo=Discord&logoColor=ffffff&style=for-the-badge" alt="Discord">
  </a>
</p>

# MaNGOS-Based Server Discord Admin

MaNGOS-Based Server Discord Admin (formerly *MaNGOS Discord Register Bot*) connects your MaNGOS server to your
Discord community. Players register and manage their accounts, see who's online, look up characters in the
armory, and get told when world events start. Staff handle GM tickets in a Discord forum, broadcast
announcements, and run restarts and bans without logging in to the server.

It works with every MaNGOS core: **Zero** (1.12), **One** (2.4.3), **Two** (3.3.5a), **Three** (4.3.4) and
**Four** (5.4.8).

**Make it yours.** Out of the box the bot calls itself **Ask MaNGOS**. Give it your server's own identity
(name, accent colour and avatar) in the `[identity]` section of `config.toml`. The bot applies the name as
its nickname in your server, and uses it in embeds and announcements.

> **Status:** beta. Test on a local server with a backup of your databases before using it in production.

## Features 🚀

**For players**
- **Registration panel**: a message with Register / How to connect / My account buttons that keeps working
  across restarts and shows live server status.
- **Safe sign-ups**: the same rules as the server itself (16-character limits, upper-casing, SHA1 hash).
  Includes a confirm-password field, or bot-generated passwords if you prefer. Also checks Discord account age,
  enforces one account per person, adds cooldowns and pauses sign-ups during mass sign-up raids.
- **`/account`**: view your account, characters and ban status; change your password; link an account made
  before the bot; choose whether you appear in the armory.
- **`/status` and `/online`**: is the server up, how many people are playing, and the faction/class breakdown.
- **`/armory`**: a character card image (a paper-doll layout like the in-game character pane) plus a gear list
  linked to Wowhead for your expansion. An **Inspect an item** menu shows full tooltips with stats.
- **`/leaderboard`**: the Hall of Legends, ranking characters by level, time played, gold or honorable kills.
- **`/events`**: world events happening now and coming up, read from your `game_event` table.

**For staff**
- **GM tickets in a forum channel**: every in-game ticket becomes a forum post with **Claim**, **Respond**
  (mails the player in game and closes the ticket) and **Close** buttons, plus Open / Claimed / Closed tags.
- **Event announcements**: posts when an event starts, a reminder before it ends, and a post when it ends, with
  varied wording. Also creates real **Discord Scheduled Events**, so players can click "Interested".
- **`/admin announce`**: post to Discord and broadcast in game at the same time.
- **`/admin restart | shutdown | cancel`**: countdown restarts with a Discord notice.
- **`/mod kick`, `/admin ban | unban`**: ban durations are checked strictly, so a typo can't become a
  permanent ban.
- **Status alerts, presence and a voice-channel player counter** (`🟢 Online: 42`).
- **Audit log channel**: registrations, links, password changes, bans and ticket replies.
- **`/admin check`**: one command that tests the databases, SOAP, channels and permissions.

## How it talks to your server

| What | How | Why |
|---|---|---|
| Reading (status, characters, tickets, events, items) | MySQL, read-only | Fast and needs no server-side changes |
| Creating accounts, changing passwords | MySQL, exactly as `AccountMgr` does | mangosd writes every SOAP command, *including passwords*, to its log |
| Ticket replies/closing, announcements, kicks, bans, restarts | SOAP | mangosd keeps tickets and sessions in memory; writing to the tables directly would be ignored or overwritten |

SOAP is optional. Without it, everything still works except the actions in the last row.

## Getting started 🔢

### 1. Install
You need **Python 3.11 or newer**, **git**, and network access from the bot to your MySQL/MariaDB server (and
to SOAP, if you use it). Running the bot on the same machine as the server is simplest.

**Linux / macOS**
```bash
git clone https://github.com/i-am-fyre/MaNGOS-Discord-Admin.git
cd MaNGOS-Discord-Admin
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/i-am-fyre/MaNGOS-Discord-Admin.git
cd MaNGOS-Discord-Admin
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
If PowerShell refuses to run `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call `.venv\Scripts\python.exe` directly.

### 2. Create the Discord bot and add it to your server
Open the [Discord Developer Portal](https://discord.com/developers/applications) and sign in with your Discord
account.

1. **Create the application.** Click **New Application**, give it a name (for example your server's name) and
   click **Create**.
2. **Copy the Application ID.** On the **General Information** page, copy the **Application ID** and keep it
   handy for the invite link below. (Discord also calls this the *Client ID*.)
3. **Get the bot token.** Open the **Bot** page from the left-hand menu:
   - Click **Reset Token**, confirm, then **Copy**. Discord shows the token only once.
   - Save it for now. In [section 5](#5-configure) you'll paste it into the `.env` file as `DISCORD_TOKEN=...`.
   - The token is the bot's password: anyone who has it controls your bot. Never put it in `config.toml`, a
     screenshot or a chat. If it leaks or you lose it, click **Reset Token** again and update `.env`.
4. **Make the bot private**, so only you can add it to servers. Do these in this order, because Discord won't
   let a private bot keep its default install link:
   - Open the **Installation** page, set **Install Link** to **None**, and click **Save Changes**.
   - Back on the **Bot** page, turn **Public Bot** off and save.
   - On the same page, leave all three **Privileged Gateway Intents** off. The bot doesn't need them.

   The invite link below still works for you afterwards. "None" only removes the *Add App* button on the
   bot's profile.
5. **Invite the bot to your server.** Paste this link into your browser, replacing `YOUR_APPLICATION_ID` with
   the Application ID you copied above:
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot+applications.commands&permissions=283803503616
   ```
   For example: `...authorize?client_id=123456789012345678&scope=bot+applications.commands&permissions=...`.
   Choose your server, check the permission list and click **Authorize**. You need the *Manage Server*
   permission on that Discord server.

   The link grants exactly what the bot needs: View Channels, Send Messages, Embed Links, Attach Files, Read
   Message History, Send Messages in Threads, Manage Roles (to give the Player role), Manage Events (for
   scheduled events) and Change Nickname (to use your `[identity]` name). **Don't give it Administrator.**
6. **Two channel-specific permissions.** In your Discord server, open each channel's settings
   (⚙️ **Edit Channel → Permissions**), add the bot, and allow:
   - on the ticket **forum** channel: *Manage Threads* (to archive, lock and tag posts) and *Manage Channel* (to
     create the Open / Claimed / Closed tags once),
   - on the player-counter **voice channel**: *Manage Channel* (to rename it).

   **Make the ticket forum staff-only.** Its posts contain what players write in their GM tickets. In its
   permissions, deny *View Channel* to @everyone and allow it for your staff role and the bot. The channel
   must be of type **Forum**, which Discord only offers on Community servers (**Server Settings → Enable
   Community**).
7. **Role order.** In **Server Settings → Roles**, drag the bot's role **above** the Player role. Discord only
   lets a bot hand out roles that are below its own.

### 3. Create a database user
Don't give the bot your `root` or `mangos` login. [`docs/database-user.sql`](docs/database-user.sql) creates a
`discordbot` user that can only read what the bot needs, and only write new accounts and password changes.
**Edit the script before you run it:**

1. **Make a private copy**, so your password never ends up in git (`*.local.sql` files are git-ignored):
   ```bash
   cp docs/database-user.sql database-user.local.sql      # Windows: copy docs\database-user.sql database-user.local.sql
   ```
2. **Open the copy and find-and-replace:**

   | Find | Replace with |
   |---|---|
   | `change-me` | a long random password. Put the same one in `.env` as `MANGOS_DB_PASSWORD=` |
   | `character0` | your characters database name |
   | `mangos0` | your world database name |
   | `realmd` | your login database name (only if it isn't `realmd`) |

   Your database names are in `mangosd.conf`, as the last part of `CharacterDatabaseInfo` and
   `WorldDatabaseInfo`. For example, `"127.0.0.1;3306;root;mangos;character0"` means `character0`. The defaults
   are `characterN` and `mangosN`, where N is 0 for Zero, 1 for One, and so on up to 4 for Four. The login
   database is `LoginDatabaseInfo` in `realmd.conf`, normally `realmd`.

   **Several realms?** Copy the characters and world blocks in the script once per realm.
3. **Run the copy as a MySQL admin** (such as `root`):
   ```bash
   mysql -u root -p < database-user.local.sql
   ```
   Or open it in HeidiSQL, MySQL Workbench or phpMyAdmin and run the whole file. To check it worked, run
   `SHOW GRANTS FOR 'discordbot'@'127.0.0.1';`.
4. **Match `config.toml` to it.** Under `[database]`, keep `host = "127.0.0.1"` and `user = "discordbot"`. In
   each `[[realm]]`, set `characters_db` and `world_db` to the names you used above.

**Bot on a different machine from MySQL?** Replace every `127.0.0.1` in your copy of the script with the bot
machine's IP address, and set `[database] host` to the MySQL server's address. MySQL must accept network
connections from that machine (check `bind-address` in the MySQL config, and your firewall). Never open MySQL
to the whole internet.

**"Access denied for user 'discordbot'@'…'"** in `/admin check` or the log: the part after `@` is the address
MySQL sees the bot connecting from. Create the user for that address instead.

### 4. (Optional) Enable SOAP
In `mangosd.conf` set `SOAP.Enabled = 1` and keep `SOAP.IP = 127.0.0.1`. Create a dedicated account with
gmlevel 3 for the bot (`account create DISCORDBOT <long-password>` then `account set gmlevel DISCORDBOT 3`).
SOAP is plain HTTP with full console rights, so never expose it to the internet. Run the bot on the same
machine, or reach it through an SSH tunnel or VPN.

### 5. Configure
```bash
cp config.example.toml config.toml      # Windows: copy config.example.toml config.toml
cp .env.example .env                    # Windows: copy .env.example .env
```
**`.env` holds the secrets.** Open it in a text editor and fill in the values, without quotes:
```ini
DISCORD_TOKEN=the-bot-token-from-section-2
MANGOS_DB_PASSWORD=the-database-password-from-section-3
SOAP_USERNAME=DISCORDBOT
SOAP_PASSWORD=the-soap-account-password-from-section-4
```
Leave the SOAP lines empty if you skipped section 4.

**`config.toml` holds everything else.** Every setting is explained in the file. The ones you must set:
- **`[bot] guild_id`**: your Discord server's ID. In Discord, open **User Settings → Advanced** and turn on
  **Developer Mode**. Then right-click your server's icon and choose **Copy Server ID**. Use the same
  right-click → **Copy ID** to get the channel and role IDs for `[channels]` and `[roles]`.
- **`[database]`** and **`[[realm]]`**: your database user and names, and your realm, to match section 3.
- **`[identity]`** and **`[server]`**: what the bot calls itself, and your server's name, realmlist and
  client version.

Then validate the config and start the bot (with the virtual environment active):

```bash
python main.py --check
python main.py
```

In Discord, run **`/admin check`**, then **`/admin panel`** in your registration channel.

**Armory icons and item effects (optional).** Set `[armory] dbc_path` to your server's extracted `dbc` folder:
the `dbc` folder inside the folder that `DataDir` in `mangosd.conf` points to. Use forward slashes, for example
`dbc_path = "E:/MaNGOS/data/dbc"`. (In TOML, a backslash inside double quotes starts an escape code.) From
that folder the bot reads:
- `ItemDisplayInfo.dbc`, for each item's icon. Icon images are downloaded from `armory.icon_url` (by default
  Wowhead's icon CDN) and cached in `data/icons`. Set `icon_url = ""` to turn downloads off.
- `Spell.dbc` (with `SpellDuration.dbc` and `SpellRadius.dbc`), so tooltips show the real effect text, such as
  *"Equip: Increases damage and healing done by magical spells and effects by up to 20."* This works for
  Zero, One and Two. Three and Four show a link to the spell instead.

### 6. Keep it running
**Linux (systemd).** Save this as `/etc/systemd/system/mangos-discord-admin.service`, adjusting the user and
paths. Then run `sudo systemctl enable --now mangos-discord-admin`.
```ini
[Unit]
Description=MaNGOS Discord Admin bot
After=network-online.target mysql.service
Wants=network-online.target

[Service]
User=mangos
WorkingDirectory=/opt/MaNGOS-Discord-Admin
ExecStart=/opt/MaNGOS-Discord-Admin/.venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```
The bot reads `config.toml` and `.env` from its working directory, and logs to `data/logs/bot.log`.

**Windows.** Create a Task Scheduler task that runs at startup:
- **Program:** `C:\path\to\MaNGOS-Discord-Admin\.venv\Scripts\python.exe`
- **Arguments:** `main.py`
- **Start in:** the `MaNGOS-Discord-Admin` folder

### Updating
```bash
git pull
pip install -r requirements.txt   # with the virtual environment active
```
Then restart the bot. Clones made under the old *MaNGOS-Discord-Register-Bot* name still work, because GitHub
redirects them. To point yours at the new name, run
`git remote set-url origin https://github.com/i-am-fyre/MaNGOS-Discord-Admin.git`.

## Commands

| Command | Who | What it does |
|---|---|---|
| `/register`, `/connect` | everyone | Create an account; connection guide |
| `/account info · password · link · privacy · unlink` | everyone | Manage your linked game account |
| `/status`, `/online`, `/events` | everyone | Server status, who's online, world events |
| `/armory <character>`, `/leaderboard <category>` | everyone | Character cards and rankings |
| `/mod tickets · whois · kick · serverinfo` | staff roles | Day-to-day moderation |
| `/admin panel · check · announce · restart · shutdown · cancel · ban · unban · unlink · event-announce · sync` | admin roles | Server administration |

`/mod` and `/admin` are hidden from members without Manage Messages / Manage Server, and the bot also checks
the role IDs in `config.toml` every time a command runs.

## Security notes 🔒
- Secrets live in `.env` or environment variables (`"env:NAME"` in the config). Never put them in `config.toml`.
  `config.toml`, `.env` and `data/` are git-ignored.
- **If a bot token has ever been in a tracked file, reset it** in the Developer Portal.
- Accounts with gmlevel > 0 can't be linked, registered or have their password changed from Discord
  (`allow_gm_accounts = false`).
- The bot never logs passwords or SOAP command arguments. Player-written text (tickets, names) is escaped
  before it's shown in Discord.
- Hidden characters get the same "not found" reply as characters that don't exist.

## Upgrading from the Register Bot
The old `config.ini` and `;summon` command are gone. Copy your values into `config.toml` (`[Server]` →
`[server]`, `[Database]` → `[database]` and `[[realm]]`), move the token to `.env`, and use `/admin panel`
instead of `;summon`. There's no message purge any more: the bot edits its panel in place.

## Development
```bash
pip install -r requirements-dev.txt
pytest
```
The tests cover account hashing (against Realm_DB's default accounts), the SOAP client (against a fake
mangosd), event scheduling, DBC parsing, config validation, storage, the card renderer, and a smoke test that
builds the whole bot and checks every command and permission.

## Support 💻
Ask questions in the official MaNGOS Discord (use **#mangos-tools**):

<a href="https://discord.gg/CzXcBXq">
<img src="https://img.shields.io/discord/286167585270005763?label=Discord&logo=Discord&logoColor=ffffff&style=for-the-badge" alt="Discord">
</a>

- [Report a bug](https://github.com/i-am-fyre/MaNGOS-Discord-Admin/issues/new?assignees=&labels=bug&template=bug_report.md&title=%5B%3Abug%3A%5D+)
- [Request a feature](https://github.com/i-am-fyre/MaNGOS-Discord-Admin/issues/new?assignees=&labels=enhancement&template=feature_request.md&title=%5B%3Asparkles%3A%5D+)

## Contributing 🎉
This is an open-source project, and contributions of any size are welcome: bug fixes, features, docs, testing
on other cores, or spreading the word.

<table>
  <tr>
    <td align="center"><a href="https://github.com/i-am-fyre/"><img src="https://avatars.githubusercontent.com/u/58180427?v=4?s=100" width="100px;" alt=""/><br /><sub><b>Fyre</b></sub></a><br /><a href="https://github.com/i-am-fyre/MaNGOS-Discord-Admin/commits?author=i-am-fyre" title="Code">💻</a></td>
  </tr>
</table>

*World of Warcraft and related names are trademarks of Blizzard Entertainment, Inc. This project is not
affiliated with or endorsed by Blizzard.*

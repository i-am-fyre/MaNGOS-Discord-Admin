-- Least-privilege MySQL/MariaDB user for the Discord bot.
--
-- The bot only needs to read most tables. Its only writes are creating
-- accounts and changing passwords (realmd.account, realmd.realmcharacters).
-- Ticket replies, bans, kicks and announcements go through SOAP instead.
--
-- Before running:
--   * replace 'change-me' with a long random password (put it in .env as MANGOS_DB_PASSWORD),
--   * change 'localhost' if the bot runs on another machine,
--   * rename realmd / character0 / mangos0 if your databases are named differently,
--   * repeat the characters/world blocks for each realm.

CREATE USER IF NOT EXISTS 'discordbot'@'localhost' IDENTIFIED BY 'change-me';

-- realmd (login database) ----------------------------------------------------
GRANT SELECT ON `realmd`.`account` TO 'discordbot'@'localhost';
GRANT INSERT (`username`, `sha_pass_hash`, `joindate`, `expansion`) ON `realmd`.`account` TO 'discordbot'@'localhost';
GRANT UPDATE (`sha_pass_hash`, `v`, `s`) ON `realmd`.`account` TO 'discordbot'@'localhost';
GRANT SELECT, INSERT ON `realmd`.`realmcharacters` TO 'discordbot'@'localhost';
GRANT SELECT ON `realmd`.`realmlist` TO 'discordbot'@'localhost';
GRANT SELECT ON `realmd`.`uptime` TO 'discordbot'@'localhost';
GRANT SELECT ON `realmd`.`account_banned` TO 'discordbot'@'localhost';

-- characters database (one block per realm) --------------------------------------
GRANT SELECT ON `character0`.`characters` TO 'discordbot'@'localhost';
GRANT SELECT ON `character0`.`character_ticket` TO 'discordbot'@'localhost';
GRANT SELECT ON `character0`.`character_inventory` TO 'discordbot'@'localhost';
GRANT SELECT ON `character0`.`guild` TO 'discordbot'@'localhost';
GRANT SELECT ON `character0`.`guild_member` TO 'discordbot'@'localhost';

-- world database (one block per realm) ----------------------------------------------
GRANT SELECT ON `mangos0`.`game_event` TO 'discordbot'@'localhost';
GRANT SELECT ON `mangos0`.`item_template` TO 'discordbot'@'localhost';

FLUSH PRIVILEGES;

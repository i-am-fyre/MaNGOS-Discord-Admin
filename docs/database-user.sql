-- =============================================================================
-- Database user for MaNGOS-Based Server Discord Admin
-- =============================================================================
-- Creates a MySQL/MariaDB login called "discordbot" that can read only what the
-- bot needs, and write only new accounts and password changes. Ticket replies,
-- bans, kicks and announcements go through SOAP instead.
--
-- DON'T RUN THIS FILE AS-IS. Make a private copy first, so your password never
-- ends up in git (*.local.sql is git-ignored):
--
--     cp docs/database-user.sql database-user.local.sql
--
-- Then, in the copy, find-and-replace:
--
--   change-me   -> a long random password. Put the same one in .env as
--                  MANGOS_DB_PASSWORD=
--   character0  -> your characters database  } the last part of
--   mangos0     -> your world database       } CharacterDatabaseInfo and
--                                              WorldDatabaseInfo in mangosd.conf
--   realmd      -> your login database (only if it isn't called realmd;
--                  see LoginDatabaseInfo in realmd.conf)
--
--   Default names: characterN and mangosN, where N is 0 for Zero, 1 for One,
--   2 for Two, 3 for Three and 4 for Four. The login database is realmd for all.
--
-- Leave 127.0.0.1 alone if the bot runs on the same machine as MySQL. If it
-- doesn't, replace every 127.0.0.1 with the bot machine's IP address (README
-- section 3 explains the rest).
--
-- More than one realm? Copy the characters and world blocks once per realm.
--
-- Run the copy as a MySQL admin user (such as root):
--
--     mysql -u root -p < database-user.local.sql
--
-- or open it in HeidiSQL, MySQL Workbench or phpMyAdmin and run the whole file.
-- =============================================================================

CREATE USER IF NOT EXISTS 'discordbot'@'127.0.0.1' IDENTIFIED BY 'change-me';

-- realmd (login database) -----------------------------------------------------
GRANT SELECT ON `realmd`.`account` TO 'discordbot'@'127.0.0.1';
GRANT INSERT (`username`, `sha_pass_hash`, `joindate`, `expansion`) ON `realmd`.`account` TO 'discordbot'@'127.0.0.1';
GRANT UPDATE (`sha_pass_hash`, `v`, `s`) ON `realmd`.`account` TO 'discordbot'@'127.0.0.1';
GRANT SELECT, INSERT ON `realmd`.`realmcharacters` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `realmd`.`realmlist` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `realmd`.`uptime` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `realmd`.`account_banned` TO 'discordbot'@'127.0.0.1';

-- characters database (copy this block for each realm) ------------------------
GRANT SELECT ON `character0`.`characters` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `character0`.`character_ticket` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `character0`.`character_inventory` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `character0`.`guild` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `character0`.`guild_member` TO 'discordbot'@'127.0.0.1';

-- world database (copy this block for each realm) -----------------------------
GRANT SELECT ON `mangos0`.`game_event` TO 'discordbot'@'127.0.0.1';
GRANT SELECT ON `mangos0`.`item_template` TO 'discordbot'@'127.0.0.1';

FLUSH PRIVILEGES;

-- Check it worked:
-- SHOW GRANTS FOR 'discordbot'@'127.0.0.1';

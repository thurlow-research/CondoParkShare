<?php
/**
 * BT ParkShare Configuration
 *
 * Copy this file and adjust settings for your environment.
 * The SQLite database is stored in the data/ directory.
 */

// Guard against re-definition (allows test bootstrap to set values first)
if (!defined('SITE_NAME')) {
    // Site settings
    define('SITE_NAME', 'BT ParkShare');
    define('SITE_URL', 'https://btparkshare.com'); // Change to your domain
    define('TIMEZONE', 'America/Los_Angeles');

    // Database (SQLite file path — kept outside web root ideally, but .htaccess protects it)
    define('DB_PATH', __DIR__ . '/data/parkshare.db');

    // Brevo API (email + SMS) — key must be set as an environment variable
    define('BREVO_API_KEY', getenv('BREVO_API_KEY') ?: '');
    define('BREVO_SMS_SENDER', getenv('BREVO_SMS_SENDER') ?: 'BTParkShare'); // max 11 alphanumeric chars

    // Email settings
    define('MAIL_FROM', 'noreply@btparkshare.com');
    define('MAIL_FROM_NAME', 'BT ParkShare');
    define('ADMIN_EMAIL', 'admin@btparkshare.com');

    // SMS notifications (set to true once Brevo SMS is configured)
    define('SMS_ENABLED', (bool)getenv('BREVO_SMS_ENABLED'));

    // Session
    define('SESSION_LIFETIME', 86400); // 24 hours

    // Cron security token — set this to a random string and use it in your cron URL
    define('CRON_TOKEN', 'CHANGE_ME_TO_RANDOM_STRING');

    date_default_timezone_set(TIMEZONE);
}

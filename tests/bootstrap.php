<?php
/**
 * Test bootstrap — sets up an in-memory test database and stubs for web-only features.
 */

// Use a temporary file for the test database (some tests need persistence across connections)
define('TEST_DB_PATH', sys_get_temp_dir() . '/bt_parkshare_test_' . getmypid() . '.db');

// Override config constants before loading any app code
define('SITE_NAME', 'BT ParkShare Test');
define('SITE_URL', 'http://localhost:8000');
define('TIMEZONE', 'America/Los_Angeles');
define('DB_PATH', TEST_DB_PATH);
define('MAIL_FROM', 'test@test.com');
define('MAIL_FROM_NAME', 'Test');
define('ADMIN_EMAIL', 'admin@test.com');
define('SESSION_LIFETIME', 86400);
define('CRON_TOKEN', 'test-cron-token');
define('BREVO_API_KEY', '');           // Disabled in tests — falls back to mail()
define('BREVO_SMS_SENDER', 'Test');
define('SMS_ENABLED', false);

date_default_timezone_set(TIMEZONE);

require_once __DIR__ . '/../vendor/autoload.php';

// Load core app files so global functions are available
require_once __DIR__ . '/../includes/db.php';

// Start a session for tests (auth.php needs it)
if (session_status() === PHP_SESSION_NONE) {
    session_start();
}

// Clean up test DB on shutdown
register_shutdown_function(function () {
    foreach (glob(TEST_DB_PATH . '*') as $f) {
        @unlink($f);
    }
});

# Deployment Guide

This guide covers deploying BT ParkShare to a GoDaddy Plesk Linux server.

## Requirements

- PHP 8.0 or higher with cURL extension
- SQLite3 PHP extension (usually included)
- Apache with `mod_rewrite` enabled
- Brevo account (free tier available) for email and SMS notifications

## Step 1: Upload Files

Upload all project files to your Plesk document root, typically:

```
/var/www/vhosts/yourdomain.com/httpdocs/
```

You can use Plesk File Manager, SFTP, or Git deployment.

## Step 2: Configure the Application

Edit `config.php` with your site-specific settings:

```php
define('SITE_URL', 'https://yourdomain.com');    // Your domain (no trailing slash)
define('MAIL_FROM', 'noreply@yourdomain.com');    // Sender email address
define('MAIL_FROM_NAME', 'BT ParkShare');         // Sender display name
define('ADMIN_EMAIL', 'admin@yourdomain.com');     // Admin email (also default login)
define('CRON_TOKEN', 'your-random-string-here');   // Random string for cron auth
```

Generate a secure cron token:

```bash
openssl rand -hex 32
```

## Step 2b: Set Up Brevo API Key (Email & SMS)

The Brevo API key is stored as a **server environment variable** — never in the codebase.

1. Sign up at [brevo.com](https://www.brevo.com/) and generate a transactional API key under **SMTP & API > API Keys**.

2. On your Plesk server, add the environment variable to Apache. Create or edit `/etc/apache2/conf.d/brevo.conf` (or your distro's equivalent):

   ```apache
   SetEnv BREVO_API_KEY "xkeysib-your-api-key-here"
   ```

   Then restart Apache:

   ```bash
   systemctl restart apache2
   ```

   **Alternative — per-vhost in Plesk:** Add the `SetEnv` directive in Plesk under **Domains > yourdomain > Apache & nginx Settings > Additional directives for HTTP** and **HTTPS**.

   **Alternative — PHP-FPM:** If using PHP-FPM instead of mod_php, add to your pool config (e.g., `/etc/php/8.x/fpm/pool.d/yourdomain.conf`):

   ```ini
   env[BREVO_API_KEY] = "xkeysib-your-api-key-here"
   ```

3. For the **cron job** (CLI context), add the variable to the crontab or a shell profile:

   ```bash
   export BREVO_API_KEY="xkeysib-your-api-key-here"
   ```

   Or prefix the cron command:

   ```
   BREVO_API_KEY=xkeysib-your-api-key-here php /var/www/vhosts/yourdomain/httpdocs/cron.php YOUR_CRON_TOKEN
   ```

4. **Verify** the key is visible to PHP:

   ```bash
   php -r "echo getenv('BREVO_API_KEY') ? 'OK' : 'NOT SET';"
   ```

> **Fallback:** If `BREVO_API_KEY` is not set, the app falls back to PHP's built-in `mail()` function for emails. SMS notifications require the Brevo API key.

### Enabling SMS Notifications

SMS is disabled by default. To enable it, set an additional environment variable:

```bash
export BREVO_SMS_ENABLED=1
```

Add this alongside `BREVO_API_KEY` in the same locations (Apache conf, PHP-FPM pool, crontab).

Optionally customize the SMS sender name (max 11 alphanumeric characters, default `BTParkShare`):

```bash
export BREVO_SMS_SENDER="BTParkShr"
```

> **Note:** Brevo SMS requires purchasing SMS credits separately from email. Phone numbers are collected at registration and should be in a format that includes country code for best results (e.g., +12065551234).

## Step 3: Set Directory Permissions

```bash
chmod 750 data/
chmod 640 config.php
```

The `data/` directory must be writable by the web server (PHP will create the SQLite database here). The `.htaccess` files block direct web access to `data/`, `includes/`, and `config.php`.

## Step 4: Initialize the Database

Visit `https://yourdomain.com/install.php` in your browser. This will:

1. Create the SQLite database at `data/parkshare.db`
2. Create the `users`, `donations`, and `bookings` tables
3. Create a default admin account

Default admin credentials:

- **Email:** The value of `ADMIN_EMAIL` in `config.php`
- **Password:** `admin123`

**Change the admin password immediately after first login.**

## Step 5: Remove install.php

After initialization, delete or rename `install.php` to prevent re-running:

```bash
rm /var/www/vhosts/yourdomain.com/httpdocs/install.php
```

## Step 6: Set Up the Cron Job

In Plesk, go to **Tools & Settings > Scheduled Tasks** (or the domain's **Scheduled Tasks** panel) and add:

- **Command:** `php /var/www/vhosts/yourdomain.com/httpdocs/cron.php YOUR_CRON_TOKEN`
- **Schedule:** Every 15 minutes (`*/15 * * * *`)

This handles sending expiry notifications when borrowed parking spots' reservation time ends.

## Step 7: Configure Email (Optional)

By default, the app uses PHP's `mail()` function. If your Plesk server has an SMTP relay configured, emails will work automatically.

If you need a specific SMTP server, configure it at the Plesk domain level under **Mail Settings**.

## Verification Checklist

- [ ] Site loads at your domain (redirects to login page)
- [ ] Admin can log in with default credentials
- [ ] Admin password has been changed
- [ ] `install.php` has been deleted
- [ ] `data/` directory is not accessible via browser (should return 403)
- [ ] `config.php` is not accessible via browser (should return 403)
- [ ] Cron job is configured and running
- [ ] `BREVO_API_KEY` environment variable is set and accessible to PHP
- [ ] Test email sending (register a test account, check admin receives notification)
- [ ] SMS sending works (if enabled — check PHP error log for issues)

## Troubleshooting

### "500 Internal Server Error"

- Check that `mod_rewrite` is enabled in Apache
- Verify PHP version is 8.0+
- Check Plesk error logs under **Logs** for the domain

### Database errors

- Ensure `data/` directory is writable by the web server user
- Check that the SQLite3 PHP extension is installed: `php -m | grep sqlite`

### Emails not sending

- Verify `BREVO_API_KEY` is set: `php -r "echo getenv('BREVO_API_KEY') ? 'OK' : 'NOT SET';"`
- Check your PHP error log for "Brevo email failed" messages
- Verify your Brevo sender email is authorized in Brevo under **Senders & IPs**
- If not using Brevo: verify PHP `mail()` function is not disabled in `php.ini`

### SMS not sending

- Verify `BREVO_SMS_ENABLED` is set to `1` in the environment
- Check your PHP error log for "Brevo SMS failed" messages
- Ensure you have SMS credits in your Brevo account
- Verify phone numbers include country code (e.g., +12065551234)

### Cron not running

- Test manually: `php /path/to/cron.php YOUR_TOKEN`
- Ensure the cron token in the command matches `CRON_TOKEN` in `config.php`
- Check that the PHP CLI path is correct (`which php`)

# BT ParkShare

A website to facilitate residents sharing parking spots for guests at Bellevue Towers Condominium.

Residents can offer their available parking spaces to fellow residents who need guest parking, making it easy to coordinate and share this limited resource within the community.

## Features

- **Account Registration** — Residents register with name, email, unit, parking spot, and phone. Accounts require admin approval.
- **Share Your Spot** — Specify date/time windows when your parking spot is available for others.
- **Find a Spot** — Search for available spots by date/time range and reserve one instantly.
- **Smart Time Splitting** — If a shared spot covers more time than requested, the remainder stays available for others.
- **Email & SMS Notifications** — Booking confirmations and expiry reminders via Brevo (email + optional SMS).
- **Admin Panel** — Manage users, view all donations/bookings, perform CRUD operations.
- **Secure** — Password hashing, CSRF protection, prepared statements, session security.

## Tech Stack

- **PHP 8+** with SQLite (no external database server needed)
- **[Brevo](https://www.brevo.com/)** for transactional email and SMS
- **Bootstrap 5** for responsive UI
- Designed for **GoDaddy Plesk** Linux hosting

## Installation

1. Upload all files to your Plesk document root (e.g., `httpdocs/`)
2. Edit `config.php` — set your domain, admin email, and cron token
3. Set the `BREVO_API_KEY` environment variable on your server (see [Deployment Guide](docs/deployment-guide.md#step-2b-set-up-brevo-api-key-email--sms))
4. Visit `https://yourdomain.com/install.php` to initialize the database
5. Log in with the default admin credentials shown, then **change the password**
6. Delete or rename `install.php`
7. Set up a cron job in Plesk (every 15 minutes):

   ```bash
   BREVO_API_KEY=xkeysib-... php /var/www/vhosts/yourdomain/httpdocs/cron.php YOUR_CRON_TOKEN
   ```

## License

This work is licensed under the [Creative Commons Attribution 4.0 International License (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

You are free to share and adapt this material for any purpose, including commercially, as long as you give appropriate attribution.

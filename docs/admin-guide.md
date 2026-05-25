# Administrator Guide

This guide covers managing BT ParkShare as a site administrator.

## Accessing the Admin Panel

Admin features appear in the navigation bar under the **Admin** dropdown menu. You must be logged in with an account that has the `admin` role.

The default admin account is created during installation:

- **Email:** The `ADMIN_EMAIL` value from `config.php`
- **Password:** `admin123` (change immediately after first login)

## Managing Users

Navigate to **Admin > Manage Users**.

### Filtering Users

Use the pill tabs to filter the user list:

- **All** — Every registered user
- **Pending** — New registrations awaiting approval (highlighted in yellow)
- **Approved** — Active users who can log in
- **Rejected** — Users whose registrations were denied

A badge on the page title shows the count of pending registrations.

### Approving or Rejecting Users

When a new user registers, their account is set to **Pending** status. They cannot log in until approved.

- Click **Approve** to activate the account — the user receives an email notification
- Click **Reject** to deny the account

Rejected users can be re-approved later by clicking **Approve** on their row.

### Promoting Users to Admin

For approved users, you can:

- Click **Make Admin** to grant admin privileges
- Click **Remove Admin** to revoke admin privileges

You cannot modify your own role to prevent accidental self-lockout.

### Deleting Users

Click **Delete** to permanently remove a user. A confirmation prompt prevents accidental deletion. You cannot delete your own account.

**Note:** Deleting a user does not automatically cancel their donations or bookings. Cancel those first if needed.

## Managing Donations & Bookings

Navigate to **Admin > All Donations**.

### Viewing Donations

The table shows every donation across all users with:

- **ID** — Database record ID
- **Spot** — Parking spot number
- **Donor** — Who shared the spot (name and unit)
- **From / Until** — The availability window
- **Status** — Available, Booked, or Cancelled
- **Reserved By** — If booked, shows borrower name, unit, and time range

### Filtering

Use the pill tabs to filter by status:

- **All** — Every donation
- **Available** — Open spots waiting for someone to reserve
- **Booked** — Spots currently reserved by someone
- **Cancelled** — Donations that were cancelled

### Admin Actions

- **Cancel** (available donations) — Sets the donation to cancelled, removing it from search results
- **Cancel Booking** (booked donations) — Cancels the active booking and restores the donation to available status
- **Delete** — Permanently removes the donation and all associated bookings from the database (requires confirmation)

## Email Notifications

As an admin, you receive an email whenever a new user registers with:

- The user's name, email, unit number, and parking spot
- A link to the pending accounts page

Configure the admin email address in `config.php`:

```php
define('ADMIN_EMAIL', 'admin@yourdomain.com');
```

## Cron Job Monitoring

The cron job (`cron.php`) runs every 15 minutes and handles:

1. Finding active bookings whose end time has passed
2. Sending expiry notification emails to borrowers
3. Marking bookings as expired (preventing duplicate notifications)

To test the cron manually:

```bash
php /path/to/cron.php YOUR_CRON_TOKEN
```

Output shows the count of processed expired bookings, e.g., `Processed 3 expired booking(s).`

## Adding Additional Admins

1. Have the user register normally
2. Go to **Admin > Manage Users**
3. **Approve** their account
4. Click **Make Admin** on their row

## Security Notes

- Change the default admin password immediately after installation
- Use a strong, unique `CRON_TOKEN` in `config.php`
- Delete `install.php` after initial setup
- Regularly review pending user registrations
- The SQLite database is stored in `data/` and protected by `.htaccess` — for maximum security, consider moving it outside the web root and updating `DB_PATH` in `config.php`

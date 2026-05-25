# Architecture & Developer Reference

Technical documentation for the BT ParkShare codebase.

## Technology Stack

| Component   | Technology                          |
|-------------|-------------------------------------|
| Language    | PHP 8.0+                            |
| Database    | SQLite 3 (via PDO)                  |
| Frontend    | Bootstrap 5.3, Bootstrap Icons      |
| Web Server  | Apache (with mod_rewrite)           |
| Hosting     | GoDaddy Plesk Linux                 |
| Email       | PHP `mail()` function               |

## Directory Structure

```
bt-parkshare/
├── admin/                  # Admin-only pages
│   ├── index.php           # Redirect to donations.php
│   ├── users.php           # User management (approve/reject/delete/roles)
│   └── donations.php       # Donation & booking management (CRUD)
├── assets/
│   └── css/
│       └── style.css       # Custom styles (extends Bootstrap)
├── data/                   # SQLite database storage (web-blocked)
│   ├── .htaccess           # "Deny from all"
│   └── .gitkeep
├── docs/                   # Documentation
├── images/                 # Logos and favicon assets
│   ├── Belleveu-Towers-Condos.jpg
│   ├── bt-high-res-logo.png
│   ├── bt-silhouette.svg
│   └── apple-touch-icon.png
├── includes/               # Shared PHP libraries (web-blocked)
│   ├── auth.php            # Authentication, sessions, CSRF
│   ├── db.php              # Database connection and schema
│   ├── email.php           # Email notification functions
│   ├── functions.php       # Utility helpers
│   ├── header.php          # HTML header, nav, flash messages
│   └── footer.php          # HTML footer, scripts
├── .htaccess               # Security rules, headers, rewrites
├── .gitignore
├── config.php              # Site configuration constants
├── favicon.ico             # Browser tab icon
├── index.php               # Entry point (redirect)
├── install.php             # One-time DB initialization
├── login.php               # Login form and auth logic
├── logout.php              # Session destruction
├── register.php            # User registration form
├── dashboard.php           # User home with stats
├── donate.php              # Share a parking spot (create/edit)
├── my-donations.php        # List user's shared spots
├── search.php              # Search available spots
├── book.php                # Reserve a spot (with time-splitting)
├── my-bookings.php         # List user's reservations
└── cron.php                # Scheduled task for expiry notifications
```

## Database Schema

The application uses a single SQLite database with three tables and foreign key relationships:

```
┌─────────────────────┐       ┌─────────────────────┐
│       users          │       │     donations        │
├─────────────────────┤       ├─────────────────────┤
│ id (PK)             │──┐    │ id (PK)             │
│ name                │  │    │ donor_id (FK)────────│──┐
│ email (UNIQUE)      │  │    │ parking_spot         │  │
│ password_hash       │  │    │ start_datetime       │  │
│ unit_number         │  │    │ end_datetime         │  │
│ parking_spot        │  │    │ status               │  │
│ phone               │  │    │ created_at           │  │
│ role                │  │    └─────────────────────┘  │
│ status              │  │                              │
│ created_at          │  │    ┌─────────────────────┐  │
└─────────────────────┘  │    │      bookings        │  │
                         │    ├─────────────────────┤  │
                         │    │ id (PK)             │  │
                         ├───│ borrower_id (FK)     │  │
                         │    │ donation_id (FK)─────│──┘
                         │    │ start_datetime       │
                         │    │ end_datetime         │
                         │    │ status               │
                         │    │ notified_expiry      │
                         │    │ created_at           │
                         │    └─────────────────────┘
```

### Table: `users`

| Column        | Type     | Notes                                           |
|---------------|----------|-------------------------------------------------|
| id            | INTEGER  | Primary key, auto-increment                     |
| name          | TEXT     | Display name                                    |
| email         | TEXT     | Unique, stored lowercase                        |
| password_hash | TEXT     | bcrypt hash via `password_hash()`               |
| unit_number   | TEXT     | Bellevue Towers unit                            |
| parking_spot  | TEXT     | Assigned parking spot number                    |
| phone         | TEXT     | Contact phone                                   |
| role          | TEXT     | `user` (default) or `admin`                     |
| status        | TEXT     | `pending` (default), `approved`, or `rejected`  |
| created_at    | DATETIME | Auto-set on insert                              |

### Table: `donations`

| Column         | Type     | Notes                                          |
|----------------|----------|-------------------------------------------------|
| id             | INTEGER  | Primary key, auto-increment                    |
| donor_id       | INTEGER  | FK → users.id                                  |
| parking_spot   | TEXT     | Spot number (denormalized from user for display)|
| start_datetime | DATETIME | When the spot becomes available                |
| end_datetime   | DATETIME | When the spot is no longer available           |
| status         | TEXT     | `available` (default), `booked`, or `cancelled`|
| created_at     | DATETIME | Auto-set on insert                             |

### Table: `bookings`

| Column          | Type     | Notes                                       |
|-----------------|----------|---------------------------------------------|
| id              | INTEGER  | Primary key, auto-increment                 |
| donation_id     | INTEGER  | FK → donations.id                           |
| borrower_id     | INTEGER  | FK → users.id                               |
| start_datetime  | DATETIME | Reservation start                           |
| end_datetime    | DATETIME | Reservation end                             |
| status          | TEXT     | `active` (default), `cancelled`, `expired`  |
| notified_expiry | INTEGER  | 0 = not notified, 1 = expiry email sent     |
| created_at      | DATETIME | Auto-set on insert                          |

### SQLite Configuration

- **Journal mode:** WAL (Write-Ahead Logging) for better concurrent read performance
- **Foreign keys:** Enabled via `PRAGMA foreign_keys=ON`
- **Error mode:** Exceptions (`PDO::ERRMODE_EXCEPTION`)
- **Fetch mode:** Associative arrays (`PDO::FETCH_ASSOC`)

## Authentication System

### Session Security

Sessions are configured in `includes/auth.php` with:

- `httponly: true` — Cookies inaccessible to JavaScript
- `samesite: Strict` — Cookies not sent on cross-origin requests
- 24-hour lifetime
- Session ID regenerated on login (`session_regenerate_id(true)`)

### Access Control

Three levels of access enforced by helper functions:

| Function         | Access Level                                  |
|------------------|-----------------------------------------------|
| `isLoggedIn()`   | Returns boolean, no redirect                  |
| `requireLogin()` | Redirects to login if not authenticated        |
| `requireAdmin()` | Redirects to dashboard if not admin role       |

Both `requireLogin()` and `requireAdmin()` verify the user's status is still `approved` on every request (prevents stale sessions for deactivated users).

### CSRF Protection

- Token: 32 bytes of `random_bytes()`, hex-encoded, stored in session
- Verification: `hash_equals()` for constant-time comparison
- Helper: `csrfField()` outputs a hidden form input
- All POST handlers check `verifyCSRFToken()` before processing

### Password Handling

- Hashed with `password_hash($password, PASSWORD_DEFAULT)` (bcrypt)
- Verified with `password_verify()`
- Minimum 8 characters enforced at registration

## Core Application Flows

### Time-Splitting Algorithm

When a borrower reserves a portion of a donation's time window, the system splits the donation to keep remaining time available:

```
Original donation: |==========| (Mon 8am - Fri 5pm)
Booking request:        |====|  (Tue 2pm - Wed 6pm)

Result:
  New donation 1: |===|         (Mon 8am - Tue 2pm)  → status: available
  Original:            |====|   (Tue 2pm - Wed 6pm)  → status: booked
  New donation 2:           |=| (Wed 6pm - Fri 5pm)  → status: available
```

Implementation in `book.php`:

1. If `requested_start > donation.start` → INSERT new available donation for the gap before
2. If `requested_end < donation.end` → INSERT new available donation for the gap after
3. UPDATE original donation: narrow to booking window, set `status='booked'`
4. INSERT booking record with `status='active'`

All four operations run inside a database transaction for atomicity.

### Booking Cancellation

When a borrower cancels (`my-bookings.php`):

1. Booking status set to `cancelled`
2. Associated donation status restored to `available`

Note: The split donations are not automatically re-merged. This is acceptable for the expected traffic level.

### Donation Search

The search query in `search.php` finds donations that **fully cover** the requested window:

```sql
WHERE status = 'available'
  AND start_datetime <= :requested_start
  AND end_datetime >= :requested_end
  AND donor_id != :current_user
```

This ensures borrowers only see spots available for their entire needed period.

## Security Architecture

### Web Server Protection (.htaccess)

```
Blocked paths (403):
  /data/*           — Database files
  /includes/*       — PHP libraries
  /config.php       — Configuration

Security headers:
  X-Content-Type-Options: nosniff
  X-Frame-Options: SAMEORIGIN
  X-XSS-Protection: 1; mode=block
  Referrer-Policy: strict-origin-when-cross-origin

Options:
  -Indexes          — No directory listings
```

### Input/Output Security

- **Input:** Prepared statements (PDO `?` placeholders) for all database queries — no string concatenation
- **Output:** `htmlspecialchars()` with `ENT_QUOTES` via `sanitize()` on all user-displayed data
- **Type casting:** Integer IDs cast with `(int)` before use

## Email System

All emails use PHP's built-in `mail()` function via `includes/email.php`. Emails are sent as HTML with a consistent template:

- Blue header bar with site name
- Content area
- Gray footer with "Bellevue Towers Condominium"

### Notification Triggers

| Event                  | Recipient | Function                           |
|------------------------|-----------|------------------------------------|
| New user registers     | Admin     | `sendNewRegistrationNotice()`      |
| Account approved       | User      | `sendAccountApprovedNotice()`      |
| Spot booked            | Donor     | `sendBookingConfirmationToDonor()` |
| Spot booked            | Borrower  | `sendBookingConfirmationToBorrower()` |
| Reservation expired    | Borrower  | `sendExpiryNotice()`               |

## Cron Job

`cron.php` is designed to run every 15 minutes. It:

1. Authenticates via token (CLI argument or query string)
2. Queries for active bookings past their `end_datetime` where `notified_expiry = 0`
3. Sends expiry email to each borrower
4. Sets `notified_expiry = 1` and `status = 'expired'`

The `notified_expiry` flag prevents duplicate emails if the cron runs multiple times.

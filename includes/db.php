<?php
require_once __DIR__ . '/../config.php';

function resetDB(): void {
    $ref = new ReflectionFunction('getDB');
    $statics = $ref->getStaticVariables();
    // Force re-creation on next call by clearing the static
    // We achieve this by using a global flag
    $GLOBALS['_db_reset'] = true;
}

function getDB(): PDO {
    static $pdo = null;
    if (isset($GLOBALS['_db_reset']) && $GLOBALS['_db_reset']) {
        $pdo = null;
        $GLOBALS['_db_reset'] = false;
    }
    if ($pdo === null) {
        $dir = dirname(DB_PATH);
        if (!is_dir($dir)) {
            mkdir($dir, 0750, true);
        }
        $pdo = new PDO('sqlite:' . DB_PATH, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA journal_mode=WAL');
        $pdo->exec('PRAGMA foreign_keys=ON');
    }
    return $pdo;
}

function initDatabase(): void {
    $db = getDB();

    $db->exec("CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        unit_number TEXT NOT NULL,
        parking_spot TEXT NOT NULL,
        phone TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )");

    $db->exec("CREATE TABLE IF NOT EXISTS donations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        donor_id INTEGER NOT NULL,
        parking_spot TEXT NOT NULL,
        start_datetime DATETIME NOT NULL,
        end_datetime DATETIME NOT NULL,
        status TEXT NOT NULL DEFAULT 'available',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (donor_id) REFERENCES users(id)
    )");

    $db->exec("CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        donation_id INTEGER NOT NULL,
        borrower_id INTEGER NOT NULL,
        start_datetime DATETIME NOT NULL,
        end_datetime DATETIME NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        notified_expiry INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (donation_id) REFERENCES donations(id),
        FOREIGN KEY (borrower_id) REFERENCES users(id)
    )");

    // Create default admin if none exists
    $stmt = $db->query("SELECT COUNT(*) as cnt FROM users WHERE role='admin'");
    if ($stmt->fetch()['cnt'] == 0) {
        $db->prepare("INSERT INTO users (name, email, password_hash, unit_number, parking_spot, phone, role, status) VALUES (?, ?, ?, ?, ?, ?, 'admin', 'approved')")
           ->execute(['Admin', ADMIN_EMAIL, password_hash('admin123', PASSWORD_DEFAULT), 'N/A', 'N/A', 'N/A']);
    }
}

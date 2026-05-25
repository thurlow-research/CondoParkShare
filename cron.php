<?php
/**
 * Cron job to handle expired bookings and send notifications.
 *
 * Set up in Plesk Scheduled Tasks (cron):
 *   php /var/www/vhosts/yourdomain/httpdocs/cron.php YOUR_CRON_TOKEN
 *
 * Or via URL (less preferred):
 *   curl https://yourdomain.com/cron.php?token=YOUR_CRON_TOKEN
 *
 * Run every 15 minutes.
 */

require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/email.php';

// Authenticate: accept token via CLI argument or query string
$token = $argv[1] ?? $_GET['token'] ?? '';
if ($token !== CRON_TOKEN || CRON_TOKEN === 'CHANGE_ME_TO_RANDOM_STRING') {
    http_response_code(403);
    echo "Unauthorized\n";
    exit(1);
}

$db = getDB();
$now = date('Y-m-d H:i:s');

// Find active bookings that have expired and haven't been notified
$stmt = $db->prepare("
    SELECT b.*, d.parking_spot, d.donor_id,
        bu.name as borrower_name, bu.email as borrower_email, bu.phone as borrower_phone
    FROM bookings b
    JOIN donations d ON d.id = b.donation_id
    JOIN users bu ON bu.id = b.borrower_id
    WHERE b.status = 'active' AND b.end_datetime <= ? AND b.notified_expiry = 0
");
$stmt->execute([$now]);
$expired = $stmt->fetchAll();

$count = 0;
foreach ($expired as $booking) {
    // Send expiry notice to borrower
    sendExpiryNotice(
        ['name' => $booking['borrower_name'], 'email' => $booking['borrower_email'], 'phone' => $booking['borrower_phone']],
        $booking,
        $booking['parking_spot']
    );

    // Mark as notified and expired
    $db->prepare("UPDATE bookings SET notified_expiry = 1, status = 'expired' WHERE id = ?")
       ->execute([$booking['id']]);

    $count++;
}

echo "Processed $count expired booking(s).\n";

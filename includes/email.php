<?php
require_once __DIR__ . '/../config.php';

/**
 * Send an email via Brevo transactional API, falling back to PHP mail() if no API key is set.
 */
function sendEmail(string $to, string $subject, string $body): bool {
    $htmlBody = '<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">';
    $htmlBody .= '<div style="background:#1a5276;color:white;padding:20px;text-align:center;">';
    $htmlBody .= '<h2>' . SITE_NAME . '</h2></div>';
    $htmlBody .= '<div style="padding:20px;border:1px solid #ddd;">' . $body . '</div>';
    $htmlBody .= '<div style="padding:10px;text-align:center;color:#888;font-size:12px;">';
    $htmlBody .= 'Bellevue Towers Condominium &mdash; Parking Spot Sharing</div>';
    $htmlBody .= '</body></html>';

    if (!empty(BREVO_API_KEY)) {
        return brevoSendEmail($to, $subject, $htmlBody);
    }

    // Fallback to PHP mail()
    $headers = [
        'From: ' . MAIL_FROM_NAME . ' <' . MAIL_FROM . '>',
        'Reply-To: ' . MAIL_FROM,
        'Content-Type: text/html; charset=UTF-8',
        'X-Mailer: BT-ParkShare',
    ];
    return mail($to, $subject, $htmlBody, implode("\r\n", $headers));
}

/**
 * Send an email using the Brevo transactional email API.
 */
function brevoSendEmail(string $to, string $subject, string $htmlContent): bool {
    $payload = json_encode([
        'sender' => ['name' => MAIL_FROM_NAME, 'email' => MAIL_FROM],
        'to' => [['email' => $to]],
        'subject' => $subject,
        'htmlContent' => $htmlContent,
    ]);

    $ch = curl_init('https://api.brevo.com/v3/smtp/email');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_HTTPHEADER => [
            'accept: application/json',
            'api-key: ' . BREVO_API_KEY,
            'content-type: application/json',
        ],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
    ]);

    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($error || $httpCode >= 400) {
        error_log("Brevo email failed (HTTP $httpCode): $error $response");
        return false;
    }
    return true;
}

/**
 * Send an SMS via Brevo transactional SMS API.
 *
 * Phone number should be in E.164 format (e.g., +12065551234).
 * Returns true on success, false on failure or if SMS is disabled.
 */
function sendSMS(string $to, string $message): bool {
    if (!defined('SMS_ENABLED') || !SMS_ENABLED || empty(BREVO_API_KEY)) {
        return false;
    }

    // Normalize phone: strip non-digit except leading +
    $phone = preg_replace('/[^\d+]/', '', $to);
    if (!str_starts_with($phone, '+')) {
        // Assume US number if no country code
        $phone = '+1' . ltrim($phone, '1');
    }

    $payload = json_encode([
        'type' => 'transactional',
        'unicodeEnabled' => false,
        'sender' => BREVO_SMS_SENDER,
        'recipient' => $phone,
        'content' => $message,
    ]);

    $ch = curl_init('https://api.brevo.com/v3/transactionalSMS/sms');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_HTTPHEADER => [
            'accept: application/json',
            'api-key: ' . BREVO_API_KEY,
            'content-type: application/json',
        ],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
    ]);

    $response = curl_exec($ch);
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($error || $httpCode >= 400) {
        error_log("Brevo SMS failed (HTTP $httpCode): $error $response");
        return false;
    }
    return true;
}

// --- Notification functions ---

function sendBookingConfirmationToDonor(array $donor, array $borrower, array $booking, string $parkingSpot): void {
    $subject = 'Your parking spot #' . $parkingSpot . ' has been reserved';
    $body = '<p>Hello ' . htmlspecialchars($donor['name']) . ',</p>';
    $body .= '<p><strong>' . htmlspecialchars($borrower['name']) . '</strong> (Unit ' . htmlspecialchars($borrower['unit_number']) . ') ';
    $body .= 'has reserved your parking spot <strong>#' . htmlspecialchars($parkingSpot) . '</strong>.</p>';
    $body .= '<p><strong>From:</strong> ' . date('M j, Y g:i A', strtotime($booking['start_datetime'])) . '<br>';
    $body .= '<strong>Until:</strong> ' . date('M j, Y g:i A', strtotime($booking['end_datetime'])) . '</p>';
    $body .= '<p>Contact: ' . htmlspecialchars($borrower['phone']) . ' / ' . htmlspecialchars($borrower['email']) . '</p>';
    sendEmail($donor['email'], $subject, $body);

    $smsMsg = 'BT ParkShare: Your spot #' . $parkingSpot . ' was reserved by '
        . $borrower['name'] . ' (Unit ' . $borrower['unit_number'] . ') from '
        . date('M j g:iA', strtotime($booking['start_datetime'])) . ' to '
        . date('M j g:iA', strtotime($booking['end_datetime']));
    sendSMS($donor['phone'], $smsMsg);
}

function sendBookingConfirmationToBorrower(array $borrower, array $donor, array $booking, string $parkingSpot): void {
    $subject = 'Parking spot #' . $parkingSpot . ' confirmed';
    $body = '<p>Hello ' . htmlspecialchars($borrower['name']) . ',</p>';
    $body .= '<p>You have reserved parking spot <strong>#' . htmlspecialchars($parkingSpot) . '</strong> ';
    $body .= 'from <strong>' . htmlspecialchars($donor['name']) . '</strong> (Unit ' . htmlspecialchars($donor['unit_number']) . ').</p>';
    $body .= '<p><strong>From:</strong> ' . date('M j, Y g:i A', strtotime($booking['start_datetime'])) . '<br>';
    $body .= '<strong>Until:</strong> ' . date('M j, Y g:i A', strtotime($booking['end_datetime'])) . '</p>';
    $body .= '<p>Contact: ' . htmlspecialchars($donor['phone']) . ' / ' . htmlspecialchars($donor['email']) . '</p>';
    sendEmail($borrower['email'], $subject, $body);

    $smsMsg = 'BT ParkShare: Spot #' . $parkingSpot . ' confirmed! '
        . date('M j g:iA', strtotime($booking['start_datetime'])) . ' to '
        . date('M j g:iA', strtotime($booking['end_datetime']))
        . '. Contact ' . $donor['name'] . ': ' . $donor['phone'];
    sendSMS($borrower['phone'], $smsMsg);
}

function sendExpiryNotice(array $borrower, array $booking, string $parkingSpot): void {
    $subject = 'Your parking reservation for spot #' . $parkingSpot . ' has expired';
    $body = '<p>Hello ' . htmlspecialchars($borrower['name']) . ',</p>';
    $body .= '<p>Your reservation for parking spot <strong>#' . htmlspecialchars($parkingSpot) . '</strong> has expired.</p>';
    $body .= '<p>The reservation ended at <strong>' . date('M j, Y g:i A', strtotime($booking['end_datetime'])) . '</strong>.</p>';
    $body .= '<p>Please ensure the spot is now available for its owner. Thank you!</p>';
    sendEmail($borrower['email'], $subject, $body);

    $smsMsg = 'BT ParkShare: Your reservation for spot #' . $parkingSpot
        . ' has expired (ended ' . date('M j g:iA', strtotime($booking['end_datetime']))
        . '). Please vacate the spot.';
    sendSMS($borrower['phone'], $smsMsg);
}

function sendAccountApprovedNotice(array $user): void {
    $subject = 'Your ' . SITE_NAME . ' account has been approved';
    $body = '<p>Hello ' . htmlspecialchars($user['name']) . ',</p>';
    $body .= '<p>Your account has been approved! You can now <a href="' . SITE_URL . '/login.php">log in</a> ';
    $body .= 'to share or find parking spots.</p>';
    sendEmail($user['email'], $subject, $body);
}

function sendNewRegistrationNotice(array $user): void {
    $subject = 'New registration pending approval: ' . $user['name'];
    $body = '<p>A new user has registered and is awaiting approval:</p>';
    $body .= '<ul>';
    $body .= '<li><strong>Name:</strong> ' . htmlspecialchars($user['name']) . '</li>';
    $body .= '<li><strong>Email:</strong> ' . htmlspecialchars($user['email']) . '</li>';
    $body .= '<li><strong>Unit:</strong> ' . htmlspecialchars($user['unit_number']) . '</li>';
    $body .= '<li><strong>Parking Spot:</strong> ' . htmlspecialchars($user['parking_spot']) . '</li>';
    $body .= '</ul>';
    $body .= '<p><a href="' . SITE_URL . '/admin/users.php">Review pending accounts</a></p>';
    sendEmail(ADMIN_EMAIL, $subject, $body);
}

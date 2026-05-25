<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/functions.php';
require_once __DIR__ . '/includes/email.php';

$user = requireLogin();
$db = getDB();

$donationId = (int)($_REQUEST['donation_id'] ?? 0);
$requestedStart = $_REQUEST['start'] ?? '';
$requestedEnd = $_REQUEST['end'] ?? '';

// Validate donation exists and is available
$stmt = $db->prepare("SELECT d.*, u.name as donor_name, u.unit_number as donor_unit, u.email as donor_email, u.phone as donor_phone
    FROM donations d JOIN users u ON u.id = d.donor_id
    WHERE d.id = ? AND d.status = 'available'");
$stmt->execute([$donationId]);
$donation = $stmt->fetch();

if (!$donation) {
    flash('error', 'This spot is no longer available.');
    header('Location: search.php');
    exit;
}

if ($donation['donor_id'] == $user['id']) {
    flash('error', 'You cannot reserve your own spot.');
    header('Location: search.php');
    exit;
}

// Verify the donation covers the requested time
if (strtotime($requestedStart) < strtotime($donation['start_datetime']) ||
    strtotime($requestedEnd) > strtotime($donation['end_datetime'])) {
    flash('error', 'The requested time is not fully covered by this donation.');
    header('Location: search.php');
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!verifyCSRFToken($_POST['csrf_token'] ?? '')) {
        flash('error', 'Invalid form submission.');
        header('Location: search.php');
        exit;
    }

    $db->beginTransaction();
    try {
        // Split donation if needed:
        // If there's time BEFORE the booking, create a new available donation for that period
        if (strtotime($requestedStart) > strtotime($donation['start_datetime'])) {
            $stmt = $db->prepare("INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime, status) VALUES (?, ?, ?, ?, 'available')");
            $stmt->execute([$donation['donor_id'], $donation['parking_spot'], $donation['start_datetime'], $requestedStart]);
        }

        // If there's time AFTER the booking, create a new available donation for that period
        if (strtotime($requestedEnd) < strtotime($donation['end_datetime'])) {
            $stmt = $db->prepare("INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime, status) VALUES (?, ?, ?, ?, 'available')");
            $stmt->execute([$donation['donor_id'], $donation['parking_spot'], $requestedEnd, $donation['end_datetime']]);
        }

        // Update the original donation to match the booked time and mark as booked
        $stmt = $db->prepare("UPDATE donations SET start_datetime = ?, end_datetime = ?, status = 'booked' WHERE id = ?");
        $stmt->execute([$requestedStart, $requestedEnd, $donation['id']]);

        // Create the booking
        $stmt = $db->prepare("INSERT INTO bookings (donation_id, borrower_id, start_datetime, end_datetime) VALUES (?, ?, ?, ?)");
        $stmt->execute([$donation['id'], $user['id'], $requestedStart, $requestedEnd]);

        $db->commit();

        // Send email notifications
        $bookingData = ['start_datetime' => $requestedStart, 'end_datetime' => $requestedEnd];
        $donorData = ['name' => $donation['donor_name'], 'email' => $donation['donor_email'], 'unit_number' => $donation['donor_unit'], 'phone' => $donation['donor_phone']];
        sendBookingConfirmationToDonor($donorData, $user, $bookingData, $donation['parking_spot']);
        sendBookingConfirmationToBorrower($user, $donorData, $bookingData, $donation['parking_spot']);

        flash('success', 'Parking spot #' . $donation['parking_spot'] . ' reserved! Check your email for details.');
        header('Location: my-bookings.php');
        exit;

    } catch (Exception $e) {
        $db->rollBack();
        flash('error', 'An error occurred. Please try again.');
        header('Location: search.php');
        exit;
    }
}

$pageTitle = 'Confirm Reservation';
require __DIR__ . '/includes/header.php';
?>

<div class="row justify-content-center">
    <div class="col-md-6">
        <h3>Confirm Reservation</h3>
        <div class="card">
            <div class="card-body">
                <table class="table table-borderless mb-0">
                    <tr><th>Parking Spot</th><td>#<?= sanitize($donation['parking_spot']) ?></td></tr>
                    <tr><th>Shared By</th><td><?= sanitize($donation['donor_name']) ?> (Unit <?= sanitize($donation['donor_unit']) ?>)</td></tr>
                    <tr><th>Your Reservation</th><td><?= formatDateTime($requestedStart) ?> &mdash; <?= formatDateTime($requestedEnd) ?></td></tr>
                    <?php if (strtotime($requestedStart) > strtotime($donation['start_datetime']) ||
                              strtotime($requestedEnd) < strtotime($donation['end_datetime'])): ?>
                    <tr>
                        <th>Note</th>
                        <td class="text-muted">The remaining time will stay available for others.</td>
                    </tr>
                    <?php endif; ?>
                </table>
            </div>
        </div>
        <form method="post" class="mt-3">
            <?= csrfField() ?>
            <input type="hidden" name="donation_id" value="<?= $donationId ?>">
            <input type="hidden" name="start" value="<?= sanitize($requestedStart) ?>">
            <input type="hidden" name="end" value="<?= sanitize($requestedEnd) ?>">
            <button type="submit" class="btn btn-success">Confirm Reservation</button>
            <a href="search.php?start=<?= urlencode($requestedStart) ?>&end=<?= urlencode($requestedEnd) ?>" class="btn btn-outline-secondary">Back to Results</a>
        </form>
    </div>
</div>

<?php require __DIR__ . '/includes/footer.php'; ?>

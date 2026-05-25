<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/functions.php';

$user = requireLogin();
$db = getDB();

// Handle cancel
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['cancel_booking_id'])) {
    if (verifyCSRFToken($_POST['csrf_token'] ?? '')) {
        $bookingId = (int)$_POST['cancel_booking_id'];
        // Get booking info to restore donation
        $stmt = $db->prepare("SELECT b.*, d.donor_id, d.parking_spot FROM bookings b JOIN donations d ON d.id = b.donation_id WHERE b.id = ? AND b.borrower_id = ? AND b.status = 'active'");
        $stmt->execute([$bookingId, $user['id']]);
        $booking = $stmt->fetch();

        if ($booking) {
            $db->beginTransaction();
            try {
                // Cancel the booking
                $stmt = $db->prepare("UPDATE bookings SET status = 'cancelled' WHERE id = ?");
                $stmt->execute([$bookingId]);

                // Restore the donation to available
                $stmt = $db->prepare("UPDATE donations SET status = 'available' WHERE id = ?");
                $stmt->execute([$booking['donation_id']]);

                $db->commit();
                flash('success', 'Reservation cancelled.');
            } catch (Exception $e) {
                $db->rollBack();
                flash('error', 'Could not cancel reservation.');
            }
        }
        header('Location: my-bookings.php');
        exit;
    }
}

$stmt = $db->prepare("
    SELECT b.*, d.parking_spot, u.name as donor_name, u.unit_number as donor_unit, u.phone as donor_phone, u.email as donor_email
    FROM bookings b
    JOIN donations d ON d.id = b.donation_id
    JOIN users u ON u.id = d.donor_id
    WHERE b.borrower_id = ?
    ORDER BY b.start_datetime DESC
");
$stmt->execute([$user['id']]);
$bookings = $stmt->fetchAll();

$pageTitle = 'My Reservations';
require __DIR__ . '/includes/header.php';
?>

<h3>My Reservations</h3>

<?php if (empty($bookings)): ?>
    <div class="alert alert-info">You haven't reserved any spots yet. <a href="search.php">Find one now!</a></div>
<?php else: ?>
    <div class="table-responsive">
        <table class="table table-hover">
            <thead>
                <tr>
                    <th>Spot #</th>
                    <th>From</th>
                    <th>Until</th>
                    <th>Shared By</th>
                    <th>Contact</th>
                    <th>Status</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
            <?php foreach ($bookings as $b): ?>
                <?php
                    $isExpired = strtotime($b['end_datetime']) < time();
                    $isActive = $b['status'] === 'active' && !$isExpired;
                ?>
                <tr class="<?= !$isActive ? 'table-light text-muted' : '' ?>">
                    <td><strong>#<?= sanitize($b['parking_spot']) ?></strong></td>
                    <td><?= formatDateTime($b['start_datetime']) ?></td>
                    <td><?= formatDateTime($b['end_datetime']) ?></td>
                    <td><?= sanitize($b['donor_name']) ?> (Unit <?= sanitize($b['donor_unit']) ?>)</td>
                    <td>
                        <small><?= sanitize($b['donor_phone']) ?></small><br>
                        <small><?= sanitize($b['donor_email']) ?></small>
                    </td>
                    <td>
                        <?php if ($b['status'] === 'cancelled'): ?>
                            <span class="badge bg-secondary">Cancelled</span>
                        <?php elseif ($isExpired): ?>
                            <span class="badge bg-secondary">Expired</span>
                        <?php else: ?>
                            <span class="badge bg-success">Active</span>
                        <?php endif; ?>
                    </td>
                    <td>
                        <?php if ($isActive): ?>
                            <form method="post" class="d-inline" onsubmit="return confirm('Cancel this reservation?')">
                                <?= csrfField() ?>
                                <input type="hidden" name="cancel_booking_id" value="<?= $b['id'] ?>">
                                <button class="btn btn-sm btn-outline-danger">Cancel</button>
                            </form>
                        <?php endif; ?>
                    </td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
    </div>
<?php endif; ?>

<?php require __DIR__ . '/includes/footer.php'; ?>

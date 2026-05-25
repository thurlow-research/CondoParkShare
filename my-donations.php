<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/functions.php';

$user = requireLogin();
$db = getDB();
$now = date('Y-m-d H:i:s');

// Handle cancel
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['cancel_id'])) {
    if (verifyCSRFToken($_POST['csrf_token'] ?? '')) {
        $stmt = $db->prepare("UPDATE donations SET status = 'cancelled' WHERE id = ? AND donor_id = ? AND status = 'available'");
        $stmt->execute([(int)$_POST['cancel_id'], $user['id']]);
        flash('success', 'Donation cancelled.');
        header('Location: my-donations.php');
        exit;
    }
}

// Active donations with booking info
$stmt = $db->prepare("
    SELECT d.*,
        b.id as booking_id, b.borrower_id, b.start_datetime as book_start, b.end_datetime as book_end, b.status as book_status,
        u.name as borrower_name, u.unit_number as borrower_unit, u.phone as borrower_phone, u.email as borrower_email
    FROM donations d
    LEFT JOIN bookings b ON b.donation_id = d.id AND b.status = 'active'
    LEFT JOIN users u ON u.id = b.borrower_id
    WHERE d.donor_id = ? AND d.status != 'cancelled'
    ORDER BY d.start_datetime ASC
");
$stmt->execute([$user['id']]);
$donations = $stmt->fetchAll();

$pageTitle = 'My Shared Spots';
require __DIR__ . '/includes/header.php';
?>

<div class="d-flex justify-content-between align-items-center mb-3">
    <h3>My Shared Spots</h3>
    <a href="donate.php" class="btn btn-primary"><i class="bi bi-plus"></i> Share My Spot</a>
</div>

<?php if (empty($donations)): ?>
    <div class="alert alert-info">You haven't shared any parking spots yet. <a href="donate.php">Share one now!</a></div>
<?php else: ?>
    <div class="table-responsive">
        <table class="table table-hover">
            <thead>
                <tr>
                    <th>Spot</th>
                    <th>Available From</th>
                    <th>Available Until</th>
                    <th>Status</th>
                    <th>Used By</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
            <?php foreach ($donations as $d): ?>
                <?php
                    $isExpired = strtotime($d['end_datetime']) < time();
                    $isBooked = $d['booking_id'] !== null;
                ?>
                <tr class="<?= $isExpired ? 'table-light text-muted' : '' ?>">
                    <td>#<?= sanitize($d['parking_spot']) ?></td>
                    <td><?= formatDateTime($d['start_datetime']) ?></td>
                    <td><?= formatDateTime($d['end_datetime']) ?></td>
                    <td>
                        <?php if ($d['status'] === 'booked'): ?>
                            <span class="badge bg-info">Reserved</span>
                        <?php elseif ($isExpired): ?>
                            <span class="badge bg-secondary">Expired</span>
                        <?php else: ?>
                            <span class="badge bg-success">Available</span>
                        <?php endif; ?>
                    </td>
                    <td>
                        <?php if ($isBooked): ?>
                            <strong><?= sanitize($d['borrower_name']) ?></strong><br>
                            <small>Unit <?= sanitize($d['borrower_unit']) ?></small><br>
                            <small><?= sanitize($d['borrower_phone']) ?></small><br>
                            <small class="text-muted"><?= formatDateTime($d['book_start']) ?> &mdash; <?= formatDateTime($d['book_end']) ?></small>
                        <?php else: ?>
                            <span class="text-muted">&mdash;</span>
                        <?php endif; ?>
                    </td>
                    <td>
                        <?php if ($d['status'] === 'available' && !$isExpired): ?>
                            <a href="donate.php?id=<?= $d['id'] ?>" class="btn btn-sm btn-outline-primary">Edit</a>
                            <form method="post" class="d-inline" onsubmit="return confirm('Cancel this shared spot?')">
                                <?= csrfField() ?>
                                <input type="hidden" name="cancel_id" value="<?= $d['id'] ?>">
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

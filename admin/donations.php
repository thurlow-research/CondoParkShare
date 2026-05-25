<?php
require_once __DIR__ . '/../includes/db.php';
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/functions.php';

$user = requireAdmin();
$db = getDB();

// Handle admin actions
if ($_SERVER['REQUEST_METHOD'] === 'POST' && verifyCSRFToken($_POST['csrf_token'] ?? '')) {
    $action = $_POST['action'] ?? '';
    $id = (int)($_POST['id'] ?? 0);

    if ($action === 'cancel_donation' && $id) {
        // Cancel donation and any active bookings
        $db->prepare("UPDATE bookings SET status = 'cancelled' WHERE donation_id = ? AND status = 'active'")->execute([$id]);
        $db->prepare("UPDATE donations SET status = 'cancelled' WHERE id = ?")->execute([$id]);
        flash('success', 'Donation cancelled.');
    } elseif ($action === 'cancel_booking' && $id) {
        $stmt = $db->prepare("SELECT donation_id FROM bookings WHERE id = ?");
        $stmt->execute([$id]);
        $booking = $stmt->fetch();
        if ($booking) {
            $db->prepare("UPDATE bookings SET status = 'cancelled' WHERE id = ?")->execute([$id]);
            $db->prepare("UPDATE donations SET status = 'available' WHERE id = ?")->execute([$booking['donation_id']]);
        }
        flash('success', 'Booking cancelled.');
    } elseif ($action === 'delete_donation' && $id) {
        $db->prepare("DELETE FROM bookings WHERE donation_id = ?")->execute([$id]);
        $db->prepare("DELETE FROM donations WHERE id = ?")->execute([$id]);
        flash('success', 'Donation deleted.');
    }
    header('Location: donations.php');
    exit;
}

$filter = $_GET['filter'] ?? 'all';
$where = '';
if ($filter === 'available') $where = "AND d.status = 'available'";
elseif ($filter === 'booked') $where = "AND d.status = 'booked'";
elseif ($filter === 'cancelled') $where = "AND d.status = 'cancelled'";

$stmt = $db->query("
    SELECT d.*,
        u.name as donor_name, u.unit_number as donor_unit,
        b.id as booking_id, b.borrower_id, b.start_datetime as book_start, b.end_datetime as book_end, b.status as book_status,
        bu.name as borrower_name, bu.unit_number as borrower_unit
    FROM donations d
    JOIN users u ON u.id = d.donor_id
    LEFT JOIN bookings b ON b.donation_id = d.id AND b.status = 'active'
    LEFT JOIN users bu ON bu.id = b.borrower_id
    WHERE 1=1 $where
    ORDER BY d.start_datetime DESC
");
$donations = $stmt->fetchAll();

$pageTitle = 'All Donations (Admin)';
require __DIR__ . '/../includes/header.php';
?>

<h3>All Parking Spot Donations</h3>

<ul class="nav nav-pills mb-3">
    <li class="nav-item"><a class="nav-link <?= $filter === 'all' ? 'active' : '' ?>" href="?filter=all">All</a></li>
    <li class="nav-item"><a class="nav-link <?= $filter === 'available' ? 'active' : '' ?>" href="?filter=available">Available</a></li>
    <li class="nav-item"><a class="nav-link <?= $filter === 'booked' ? 'active' : '' ?>" href="?filter=booked">Booked</a></li>
    <li class="nav-item"><a class="nav-link <?= $filter === 'cancelled' ? 'active' : '' ?>" href="?filter=cancelled">Cancelled</a></li>
</ul>

<?php if (empty($donations)): ?>
    <div class="alert alert-info">No donations found.</div>
<?php else: ?>
    <div class="table-responsive">
        <table class="table table-hover table-sm">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Spot</th>
                    <th>Donor</th>
                    <th>From</th>
                    <th>Until</th>
                    <th>Status</th>
                    <th>Reserved By</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
            <?php foreach ($donations as $d): ?>
                <tr>
                    <td><?= $d['id'] ?></td>
                    <td><strong>#<?= sanitize($d['parking_spot']) ?></strong></td>
                    <td><?= sanitize($d['donor_name']) ?> (Unit <?= sanitize($d['donor_unit']) ?>)</td>
                    <td><?= formatDateTime($d['start_datetime']) ?></td>
                    <td><?= formatDateTime($d['end_datetime']) ?></td>
                    <td>
                        <?php if ($d['status'] === 'available'): ?><span class="badge bg-success">Available</span>
                        <?php elseif ($d['status'] === 'booked'): ?><span class="badge bg-info">Booked</span>
                        <?php else: ?><span class="badge bg-secondary">Cancelled</span>
                        <?php endif; ?>
                    </td>
                    <td>
                        <?php if ($d['booking_id']): ?>
                            <?= sanitize($d['borrower_name']) ?> (Unit <?= sanitize($d['borrower_unit']) ?>)<br>
                            <small class="text-muted"><?= formatDateTime($d['book_start']) ?> &mdash; <?= formatDateTime($d['book_end']) ?></small>
                        <?php else: ?>
                            <span class="text-muted">&mdash;</span>
                        <?php endif; ?>
                    </td>
                    <td>
                        <div class="btn-group btn-group-sm">
                            <?php if ($d['status'] === 'available'): ?>
                                <form method="post" class="d-inline"><?= csrfField() ?><input type="hidden" name="id" value="<?= $d['id'] ?>"><input type="hidden" name="action" value="cancel_donation"><button class="btn btn-outline-warning btn-sm">Cancel</button></form>
                            <?php endif; ?>
                            <?php if ($d['booking_id'] && $d['book_status'] === 'active'): ?>
                                <form method="post" class="d-inline"><?= csrfField() ?><input type="hidden" name="id" value="<?= $d['booking_id'] ?>"><input type="hidden" name="action" value="cancel_booking"><button class="btn btn-outline-danger btn-sm">Cancel Booking</button></form>
                            <?php endif; ?>
                            <form method="post" class="d-inline" onsubmit="return confirm('Permanently delete this donation and its bookings?')"><?= csrfField() ?><input type="hidden" name="id" value="<?= $d['id'] ?>"><input type="hidden" name="action" value="delete_donation"><button class="btn btn-outline-danger btn-sm">Delete</button></form>
                        </div>
                    </td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
    </div>
<?php endif; ?>

<?php require __DIR__ . '/../includes/footer.php'; ?>

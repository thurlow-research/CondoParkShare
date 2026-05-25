<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/functions.php';

$user = requireLogin();
$db = getDB();
$results = null;
$start = $_GET['start'] ?? '';
$end = $_GET['end'] ?? '';

if (!empty($start) && !empty($end)) {
    if (strtotime($end) <= strtotime($start)) {
        flash('error', 'End time must be after start time.');
    } else {
        // Find available donations that fully cover the requested time range
        // Exclude spots donated by the current user
        $stmt = $db->prepare("
            SELECT d.*, u.name as donor_name, u.unit_number as donor_unit
            FROM donations d
            JOIN users u ON u.id = d.donor_id
            WHERE d.status = 'available'
              AND d.start_datetime <= ?
              AND d.end_datetime >= ?
              AND d.donor_id != ?
            ORDER BY d.parking_spot ASC
        ");
        $stmt->execute([$start, $end, $user['id']]);
        $results = $stmt->fetchAll();
    }
}

$pageTitle = 'Find a Spot';
require __DIR__ . '/includes/header.php';
?>

<h3>Find Guest Parking</h3>
<p class="text-muted">Enter when you need a spot, and we'll show available options.</p>

<form method="get" class="card card-body bg-light mb-4">
    <div class="row g-3 align-items-end">
        <div class="col-md-4">
            <label for="start" class="form-label">Need spot from</label>
            <input type="datetime-local" class="form-control" id="start" name="start" value="<?= sanitize($start) ?>" required>
        </div>
        <div class="col-md-4">
            <label for="end" class="form-label">Until</label>
            <input type="datetime-local" class="form-control" id="end" name="end" value="<?= sanitize($end) ?>" required>
        </div>
        <div class="col-md-4">
            <button type="submit" class="btn btn-success w-100"><i class="bi bi-search"></i> Search</button>
        </div>
    </div>
</form>

<?php if ($results !== null): ?>
    <?php if (empty($results)): ?>
        <div class="alert alert-warning">No spots available for that time range. Try different dates or a shorter period.</div>
    <?php else: ?>
        <h5><?= count($results) ?> spot<?= count($results) !== 1 ? 's' : '' ?> available</h5>
        <div class="table-responsive">
            <table class="table table-hover">
                <thead>
                    <tr>
                        <th>Spot #</th>
                        <th>Shared By</th>
                        <th>Available From</th>
                        <th>Available Until</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
                <?php foreach ($results as $r): ?>
                    <tr>
                        <td><strong>#<?= sanitize($r['parking_spot']) ?></strong></td>
                        <td><?= sanitize($r['donor_name']) ?> (Unit <?= sanitize($r['donor_unit']) ?>)</td>
                        <td><?= formatDateTime($r['start_datetime']) ?></td>
                        <td><?= formatDateTime($r['end_datetime']) ?></td>
                        <td>
                            <a href="book.php?donation_id=<?= $r['id'] ?>&start=<?= urlencode($start) ?>&end=<?= urlencode($end) ?>"
                               class="btn btn-sm btn-success">Reserve</a>
                        </td>
                    </tr>
                <?php endforeach; ?>
                </tbody>
            </table>
        </div>
    <?php endif; ?>
<?php endif; ?>

<?php require __DIR__ . '/includes/footer.php'; ?>

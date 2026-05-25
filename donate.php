<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/functions.php';

$user = requireLogin();
$errors = [];
$editing = false;
$donation = null;

$db = getDB();

// Editing an existing donation?
if (isset($_GET['id'])) {
    $stmt = $db->prepare("SELECT * FROM donations WHERE id = ? AND donor_id = ? AND status = 'available'");
    $stmt->execute([(int)$_GET['id'], $user['id']]);
    $donation = $stmt->fetch();
    if ($donation) {
        $editing = true;
    }
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!verifyCSRFToken($_POST['csrf_token'] ?? '')) {
        $errors[] = 'Invalid form submission.';
    } else {
        $start = trim($_POST['start_datetime'] ?? '');
        $end = trim($_POST['end_datetime'] ?? '');

        if (empty($start) || empty($end)) {
            $errors[] = 'Start and end date/time are required.';
        } elseif (strtotime($end) <= strtotime($start)) {
            $errors[] = 'End time must be after start time.';
        } elseif (!$editing && strtotime($start) < time()) {
            $errors[] = 'Start time cannot be in the past.';
        }

        if (empty($errors)) {
            if ($editing) {
                $stmt = $db->prepare("UPDATE donations SET start_datetime = ?, end_datetime = ? WHERE id = ? AND donor_id = ?");
                $stmt->execute([$start, $end, $donation['id'], $user['id']]);
                flash('success', 'Donation updated successfully.');
            } else {
                $stmt = $db->prepare("INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime) VALUES (?, ?, ?, ?)");
                $stmt->execute([$user['id'], $user['parking_spot'], $start, $end]);
                flash('success', 'Your parking spot has been shared! Others can now reserve it.');
            }
            header('Location: my-donations.php');
            exit;
        }
    }
}

$pageTitle = $editing ? 'Edit Shared Spot' : 'Share My Spot';
require __DIR__ . '/includes/header.php';
?>

<div class="row justify-content-center">
    <div class="col-md-6">
        <h3><?= $editing ? 'Edit Shared Spot' : 'Share Your Parking Spot' ?></h3>
        <p class="text-muted">Spot #<?= sanitize($user['parking_spot']) ?> &mdash; Specify when others can use your spot.</p>

        <?php if ($errors): ?>
            <div class="alert alert-danger">
                <ul class="mb-0"><?php foreach ($errors as $e): ?><li><?= sanitize($e) ?></li><?php endforeach; ?></ul>
            </div>
        <?php endif; ?>

        <form method="post">
            <?= csrfField() ?>
            <div class="mb-3">
                <label for="start_datetime" class="form-label">Available From</label>
                <input type="datetime-local" class="form-control" id="start_datetime" name="start_datetime"
                       value="<?= $editing ? date('Y-m-d\TH:i', strtotime($donation['start_datetime'])) : '' ?>" required>
            </div>
            <div class="mb-3">
                <label for="end_datetime" class="form-label">Available Until</label>
                <input type="datetime-local" class="form-control" id="end_datetime" name="end_datetime"
                       value="<?= $editing ? date('Y-m-d\TH:i', strtotime($donation['end_datetime'])) : '' ?>" required>
            </div>
            <button type="submit" class="btn btn-primary"><?= $editing ? 'Update' : 'Share My Spot' ?></button>
            <a href="my-donations.php" class="btn btn-outline-secondary">Cancel</a>
        </form>
    </div>
</div>

<?php require __DIR__ . '/includes/footer.php'; ?>

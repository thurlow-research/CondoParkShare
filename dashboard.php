<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/functions.php';

$user = requireLogin();
$db = getDB();
$now = date('Y-m-d H:i:s');

// My active donations
$stmt = $db->prepare("SELECT COUNT(*) as cnt FROM donations WHERE donor_id = ? AND status = 'available' AND end_datetime > ?");
$stmt->execute([$user['id'], $now]);
$activeDonations = $stmt->fetch()['cnt'];

// My spots being used
$stmt = $db->prepare("SELECT COUNT(*) as cnt FROM bookings b
    JOIN donations d ON b.donation_id = d.id
    WHERE d.donor_id = ? AND b.status = 'active' AND b.end_datetime > ?");
$stmt->execute([$user['id'], $now]);
$spotsInUse = $stmt->fetch()['cnt'];

// My active reservations
$stmt = $db->prepare("SELECT COUNT(*) as cnt FROM bookings WHERE borrower_id = ? AND status = 'active' AND end_datetime > ?");
$stmt->execute([$user['id'], $now]);
$myReservations = $stmt->fetch()['cnt'];

// Available spots right now
$stmt = $db->prepare("SELECT COUNT(*) as cnt FROM donations WHERE status = 'available' AND start_datetime <= ? AND end_datetime > ? AND donor_id != ?");
$stmt->execute([$now, $now, $user['id']]);
$availableNow = $stmt->fetch()['cnt'];

$pageTitle = 'Dashboard';
require __DIR__ . '/includes/header.php';
?>

<h2>Welcome, <?= sanitize($user['name']) ?></h2>
<p class="text-muted">Unit <?= sanitize($user['unit_number']) ?> &mdash; Parking Spot #<?= sanitize($user['parking_spot']) ?></p>

<div class="row g-4 mt-2">
    <div class="col-md-3 col-sm-6">
        <div class="card text-center border-primary">
            <div class="card-body">
                <h1 class="text-primary"><?= $activeDonations ?></h1>
                <p class="mb-0">My Shared Spots</p>
            </div>
            <a href="my-donations.php" class="card-footer text-decoration-none">View details &rarr;</a>
        </div>
    </div>
    <div class="col-md-3 col-sm-6">
        <div class="card text-center border-info">
            <div class="card-body">
                <h1 class="text-info"><?= $spotsInUse ?></h1>
                <p class="mb-0">My Spots In Use</p>
            </div>
            <a href="my-donations.php" class="card-footer text-decoration-none">View details &rarr;</a>
        </div>
    </div>
    <div class="col-md-3 col-sm-6">
        <div class="card text-center border-success">
            <div class="card-body">
                <h1 class="text-success"><?= $myReservations ?></h1>
                <p class="mb-0">My Reservations</p>
            </div>
            <a href="my-bookings.php" class="card-footer text-decoration-none">View details &rarr;</a>
        </div>
    </div>
    <div class="col-md-3 col-sm-6">
        <div class="card text-center border-warning">
            <div class="card-body">
                <h1 class="text-warning"><?= $availableNow ?></h1>
                <p class="mb-0">Available Now</p>
            </div>
            <a href="search.php" class="card-footer text-decoration-none">Find a spot &rarr;</a>
        </div>
    </div>
</div>

<div class="row mt-4 g-4">
    <div class="col-md-6">
        <div class="card">
            <div class="card-body text-center">
                <i class="bi bi-gift display-4 text-primary"></i>
                <h5 class="mt-2">Share Your Parking Spot</h5>
                <p class="text-muted">Going away? Let a neighbor use your spot for their guests.</p>
                <a href="donate.php" class="btn btn-primary">Share My Spot</a>
            </div>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card">
            <div class="card-body text-center">
                <i class="bi bi-search display-4 text-success"></i>
                <h5 class="mt-2">Find Guest Parking</h5>
                <p class="text-muted">Need a spot for your guests? See what's available.</p>
                <a href="search.php" class="btn btn-success">Search Spots</a>
            </div>
        </div>
    </div>
</div>

<?php require __DIR__ . '/includes/footer.php'; ?>

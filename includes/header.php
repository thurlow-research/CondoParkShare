<?php
require_once __DIR__ . '/auth.php';
require_once __DIR__ . '/functions.php';
startSecureSession();
$currentUser = $_SESSION['user_id'] ?? null;
$currentRole = $_SESSION['user_role'] ?? null;
$currentPage = basename($_SERVER['SCRIPT_NAME'], '.php');
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><?= isset($pageTitle) ? sanitize($pageTitle) . ' — ' : '' ?><?= SITE_NAME ?></title>
    <link rel="icon" type="image/x-icon" href="<?= SITE_URL ?>/favicon.ico">
    <link rel="icon" type="image/svg+xml" href="<?= SITE_URL ?>/images/bt-silhouette.svg">
    <link rel="apple-touch-icon" href="<?= SITE_URL ?>/images/apple-touch-icon.png">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
    <link href="<?= SITE_URL ?>/assets/css/style.css" rel="stylesheet">
</head>
<body>
<nav class="navbar navbar-expand-lg navbar-dark bg-primary">
    <div class="container">
        <a class="navbar-brand d-flex align-items-center" href="dashboard.php">
            <img src="<?= SITE_URL ?>/images/bt-high-res-logo.png" alt="BT" height="32" class="me-2">
            <?= SITE_NAME ?>
        </a>
        <?php if ($currentUser): ?>
        <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
            <span class="navbar-toggler-icon"></span>
        </button>
        <div class="collapse navbar-collapse" id="navbarNav">
            <ul class="navbar-nav me-auto">
                <li class="nav-item">
                    <a class="nav-link <?= $currentPage === 'dashboard' ? 'active' : '' ?>" href="dashboard.php">
                        <i class="bi bi-house"></i> Dashboard
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link <?= $currentPage === 'donate' ? 'active' : '' ?>" href="donate.php">
                        <i class="bi bi-gift"></i> Share My Spot
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link <?= $currentPage === 'my-donations' ? 'active' : '' ?>" href="my-donations.php">
                        <i class="bi bi-list-check"></i> My Shared Spots
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link <?= $currentPage === 'search' ? 'active' : '' ?>" href="search.php">
                        <i class="bi bi-search"></i> Find a Spot
                    </a>
                </li>
                <li class="nav-item">
                    <a class="nav-link <?= $currentPage === 'my-bookings' ? 'active' : '' ?>" href="my-bookings.php">
                        <i class="bi bi-bookmark"></i> My Reservations
                    </a>
                </li>
                <?php if ($currentRole === 'admin'): ?>
                <li class="nav-item dropdown">
                    <a class="nav-link dropdown-toggle <?= str_starts_with($currentPage, 'admin') ? 'active' : '' ?>" href="#" data-bs-toggle="dropdown">
                        <i class="bi bi-gear"></i> Admin
                    </a>
                    <ul class="dropdown-menu">
                        <li><a class="dropdown-item" href="admin/users.php">Manage Users</a></li>
                        <li><a class="dropdown-item" href="admin/donations.php">All Donations</a></li>
                    </ul>
                </li>
                <?php endif; ?>
            </ul>
            <ul class="navbar-nav">
                <li class="nav-item">
                    <a class="nav-link" href="logout.php"><i class="bi bi-box-arrow-right"></i> Logout</a>
                </li>
            </ul>
        </div>
        <?php endif; ?>
    </div>
</nav>
<div class="container mt-4">
    <?php $flash = getFlash(); if ($flash): ?>
        <div class="alert alert-<?= $flash['type'] === 'error' ? 'danger' : sanitize($flash['type']) ?> alert-dismissible fade show">
            <?= $flash['message'] ?>
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        </div>
    <?php endif; ?>

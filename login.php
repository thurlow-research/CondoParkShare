<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/functions.php';

initDatabase();

$error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!verifyCSRFToken($_POST['csrf_token'] ?? '')) {
        $error = 'Invalid form submission. Please try again.';
    } else {
        $result = loginUser($_POST['email'] ?? '', $_POST['password'] ?? '');
        if ($result === false) {
            $error = 'Invalid email or password.';
        } elseif (isset($result['error']) && $result['error'] === 'pending') {
            $error = 'Your account is pending administrator approval.';
        } else {
            header('Location: dashboard.php');
            exit;
        }
    }
} elseif (isset($_GET['error']) && $_GET['error'] === 'inactive') {
    $error = 'Your account is no longer active. Please contact the administrator.';
}

$pageTitle = 'Log In';
require __DIR__ . '/includes/header.php';
?>

<div class="row justify-content-center">
    <div class="col-md-5">
        <div class="card shadow-sm">
            <div class="card-body">
                <div class="text-center mb-4">
                    <img src="images/bt-high-res-logo.png" alt="Bellevue Towers" height="64" class="mb-2">
                    <h3 class="card-title">BT ParkShare</h3>
                    <p class="text-muted">Guest Parking Spot Sharing</p>
                </div>

                <?php if ($error): ?>
                    <div class="alert alert-danger"><?= sanitize($error) ?></div>
                <?php endif; ?>

                <form method="post" novalidate>
                    <?= csrfField() ?>
                    <div class="mb-3">
                        <label for="email" class="form-label">Email Address</label>
                        <input type="email" class="form-control" id="email" name="email" required autofocus>
                    </div>
                    <div class="mb-3">
                        <label for="password" class="form-label">Password</label>
                        <input type="password" class="form-control" id="password" name="password" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Log In</button>
                </form>
                <p class="text-center mt-3"><a href="register.php">Need an account? Register</a></p>
            </div>
        </div>
    </div>
</div>

<?php require __DIR__ . '/includes/footer.php'; ?>

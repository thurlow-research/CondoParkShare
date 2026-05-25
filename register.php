<?php
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/email.php';
require_once __DIR__ . '/includes/functions.php';

initDatabase();

$errors = [];
$old = [];

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!verifyCSRFToken($_POST['csrf_token'] ?? '')) {
        $errors[] = 'Invalid form submission. Please try again.';
    } else {
        $name = trim($_POST['name'] ?? '');
        $email = strtolower(trim($_POST['email'] ?? ''));
        $password = $_POST['password'] ?? '';
        $password_confirm = $_POST['password_confirm'] ?? '';
        $unit_number = trim($_POST['unit_number'] ?? '');
        $parking_spot = trim($_POST['parking_spot'] ?? '');
        $phone = trim($_POST['phone'] ?? '');

        $old = compact('name', 'email', 'unit_number', 'parking_spot', 'phone');

        if (empty($name)) $errors[] = 'Name is required.';
        if (!filter_var($email, FILTER_VALIDATE_EMAIL)) $errors[] = 'Valid email is required.';
        if (strlen($password) < 8) $errors[] = 'Password must be at least 8 characters.';
        if ($password !== $password_confirm) $errors[] = 'Passwords do not match.';
        if (empty($unit_number)) $errors[] = 'Unit number is required.';
        if (empty($parking_spot)) $errors[] = 'Parking spot number is required.';
        if (empty($phone)) $errors[] = 'Phone number is required.';

        if (empty($errors)) {
            $db = getDB();
            $stmt = $db->prepare("SELECT id FROM users WHERE email = ?");
            $stmt->execute([$email]);
            if ($stmt->fetch()) {
                $errors[] = 'An account with this email already exists.';
            }
        }

        if (empty($errors)) {
            $db = getDB();
            $stmt = $db->prepare("INSERT INTO users (name, email, password_hash, unit_number, parking_spot, phone) VALUES (?, ?, ?, ?, ?, ?)");
            $stmt->execute([$name, $email, password_hash($password, PASSWORD_DEFAULT), $unit_number, $parking_spot, $phone]);

            $newUser = ['name' => $name, 'email' => $email, 'unit_number' => $unit_number, 'parking_spot' => $parking_spot];
            sendNewRegistrationNotice($newUser);

            flash('success', 'Registration submitted! Your account must be approved by the administrator before you can log in.');
            header('Location: login.php');
            exit;
        }
    }
}

$pageTitle = 'Register';
require __DIR__ . '/includes/header.php';
?>

<div class="row justify-content-center">
    <div class="col-md-6">
        <div class="card shadow-sm">
            <div class="card-body">
                <h3 class="card-title text-center mb-4">Create Account</h3>
                <p class="text-muted text-center">Join BT ParkShare to share and find guest parking at Bellevue Towers.</p>

                <?php if ($errors): ?>
                    <div class="alert alert-danger">
                        <ul class="mb-0"><?php foreach ($errors as $e): ?><li><?= sanitize($e) ?></li><?php endforeach; ?></ul>
                    </div>
                <?php endif; ?>

                <form method="post" novalidate>
                    <?= csrfField() ?>
                    <div class="mb-3">
                        <label for="name" class="form-label">Full Name</label>
                        <input type="text" class="form-control" id="name" name="name" value="<?= sanitize($old['name'] ?? '') ?>" required>
                    </div>
                    <div class="mb-3">
                        <label for="email" class="form-label">Email Address</label>
                        <input type="email" class="form-control" id="email" name="email" value="<?= sanitize($old['email'] ?? '') ?>" required>
                    </div>
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <label for="password" class="form-label">Password</label>
                            <input type="password" class="form-control" id="password" name="password" minlength="8" required>
                            <div class="form-text">At least 8 characters.</div>
                        </div>
                        <div class="col-md-6 mb-3">
                            <label for="password_confirm" class="form-label">Confirm Password</label>
                            <input type="password" class="form-control" id="password_confirm" name="password_confirm" required>
                        </div>
                    </div>
                    <div class="row">
                        <div class="col-md-4 mb-3">
                            <label for="unit_number" class="form-label">Unit Number</label>
                            <input type="text" class="form-control" id="unit_number" name="unit_number" value="<?= sanitize($old['unit_number'] ?? '') ?>" required>
                        </div>
                        <div class="col-md-4 mb-3">
                            <label for="parking_spot" class="form-label">Parking Spot #</label>
                            <input type="text" class="form-control" id="parking_spot" name="parking_spot" value="<?= sanitize($old['parking_spot'] ?? '') ?>" required>
                        </div>
                        <div class="col-md-4 mb-3">
                            <label for="phone" class="form-label">Phone</label>
                            <input type="tel" class="form-control" id="phone" name="phone" value="<?= sanitize($old['phone'] ?? '') ?>" required>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Register</button>
                </form>
                <p class="text-center mt-3"><a href="login.php">Already have an account? Log in</a></p>
            </div>
        </div>
    </div>
</div>

<?php require __DIR__ . '/includes/footer.php'; ?>

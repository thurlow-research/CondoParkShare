<?php
require_once __DIR__ . '/../includes/db.php';
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/functions.php';
require_once __DIR__ . '/../includes/email.php';

$user = requireAdmin();
$db = getDB();

// Handle actions
if ($_SERVER['REQUEST_METHOD'] === 'POST' && verifyCSRFToken($_POST['csrf_token'] ?? '')) {
    $targetId = (int)($_POST['user_id'] ?? 0);
    $action = $_POST['action'] ?? '';

    if ($targetId && $targetId !== $user['id']) {
        switch ($action) {
            case 'approve':
                $db->prepare("UPDATE users SET status = 'approved' WHERE id = ?")->execute([$targetId]);
                $stmt = $db->prepare("SELECT * FROM users WHERE id = ?");
                $stmt->execute([$targetId]);
                $approvedUser = $stmt->fetch();
                if ($approvedUser) sendAccountApprovedNotice($approvedUser);
                flash('success', 'User approved.');
                break;
            case 'reject':
                $db->prepare("UPDATE users SET status = 'rejected' WHERE id = ?")->execute([$targetId]);
                flash('success', 'User rejected.');
                break;
            case 'make_admin':
                $db->prepare("UPDATE users SET role = 'admin' WHERE id = ?")->execute([$targetId]);
                flash('success', 'User promoted to admin.');
                break;
            case 'remove_admin':
                $db->prepare("UPDATE users SET role = 'user' WHERE id = ?")->execute([$targetId]);
                flash('success', 'Admin role removed.');
                break;
            case 'delete':
                $db->prepare("DELETE FROM users WHERE id = ? AND id != ?")->execute([$targetId, $user['id']]);
                flash('success', 'User deleted.');
                break;
        }
    }
    header('Location: users.php');
    exit;
}

$filter = $_GET['filter'] ?? 'all';
$where = '';
if ($filter === 'pending') $where = "WHERE status = 'pending'";
elseif ($filter === 'approved') $where = "WHERE status = 'approved'";
elseif ($filter === 'rejected') $where = "WHERE status = 'rejected'";

$users = $db->query("SELECT * FROM users $where ORDER BY created_at DESC")->fetchAll();

$pendingCount = $db->query("SELECT COUNT(*) as cnt FROM users WHERE status = 'pending'")->fetch()['cnt'];

$pageTitle = 'Manage Users';
require __DIR__ . '/../includes/header.php';
?>

<h3>Manage Users <?php if ($pendingCount > 0): ?><span class="badge bg-warning"><?= $pendingCount ?> pending</span><?php endif; ?></h3>

<ul class="nav nav-pills mb-3">
    <li class="nav-item"><a class="nav-link <?= $filter === 'all' ? 'active' : '' ?>" href="?filter=all">All</a></li>
    <li class="nav-item"><a class="nav-link <?= $filter === 'pending' ? 'active' : '' ?>" href="?filter=pending">Pending <?php if ($pendingCount): ?><span class="badge bg-light text-dark"><?= $pendingCount ?></span><?php endif; ?></a></li>
    <li class="nav-item"><a class="nav-link <?= $filter === 'approved' ? 'active' : '' ?>" href="?filter=approved">Approved</a></li>
    <li class="nav-item"><a class="nav-link <?= $filter === 'rejected' ? 'active' : '' ?>" href="?filter=rejected">Rejected</a></li>
</ul>

<?php if (empty($users)): ?>
    <div class="alert alert-info">No users found.</div>
<?php else: ?>
    <div class="table-responsive">
        <table class="table table-hover table-sm">
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Unit</th>
                    <th>Spot</th>
                    <th>Phone</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Registered</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
            <?php foreach ($users as $u): ?>
                <tr class="<?= $u['status'] === 'pending' ? 'table-warning' : '' ?>">
                    <td><?= sanitize($u['name']) ?></td>
                    <td><small><?= sanitize($u['email']) ?></small></td>
                    <td><?= sanitize($u['unit_number']) ?></td>
                    <td>#<?= sanitize($u['parking_spot']) ?></td>
                    <td><small><?= sanitize($u['phone']) ?></small></td>
                    <td><span class="badge <?= $u['role'] === 'admin' ? 'bg-primary' : 'bg-secondary' ?>"><?= $u['role'] ?></span></td>
                    <td>
                        <?php if ($u['status'] === 'pending'): ?><span class="badge bg-warning text-dark">Pending</span>
                        <?php elseif ($u['status'] === 'approved'): ?><span class="badge bg-success">Approved</span>
                        <?php else: ?><span class="badge bg-danger">Rejected</span>
                        <?php endif; ?>
                    </td>
                    <td><small><?= date('M j, Y', strtotime($u['created_at'])) ?></small></td>
                    <td>
                        <?php if ($u['id'] !== $user['id']): ?>
                        <div class="btn-group btn-group-sm">
                            <?php if ($u['status'] === 'pending'): ?>
                                <form method="post" class="d-inline"><?= csrfField() ?><input type="hidden" name="user_id" value="<?= $u['id'] ?>"><input type="hidden" name="action" value="approve"><button class="btn btn-success btn-sm">Approve</button></form>
                                <form method="post" class="d-inline"><?= csrfField() ?><input type="hidden" name="user_id" value="<?= $u['id'] ?>"><input type="hidden" name="action" value="reject"><button class="btn btn-danger btn-sm">Reject</button></form>
                            <?php elseif ($u['status'] === 'rejected'): ?>
                                <form method="post" class="d-inline"><?= csrfField() ?><input type="hidden" name="user_id" value="<?= $u['id'] ?>"><input type="hidden" name="action" value="approve"><button class="btn btn-success btn-sm">Approve</button></form>
                            <?php endif; ?>
                            <?php if ($u['status'] === 'approved'): ?>
                                <?php if ($u['role'] !== 'admin'): ?>
                                    <form method="post" class="d-inline"><?= csrfField() ?><input type="hidden" name="user_id" value="<?= $u['id'] ?>"><input type="hidden" name="action" value="make_admin"><button class="btn btn-outline-primary btn-sm">Make Admin</button></form>
                                <?php else: ?>
                                    <form method="post" class="d-inline"><?= csrfField() ?><input type="hidden" name="user_id" value="<?= $u['id'] ?>"><input type="hidden" name="action" value="remove_admin"><button class="btn btn-outline-secondary btn-sm">Remove Admin</button></form>
                                <?php endif; ?>
                            <?php endif; ?>
                            <form method="post" class="d-inline" onsubmit="return confirm('Delete this user permanently?')"><?= csrfField() ?><input type="hidden" name="user_id" value="<?= $u['id'] ?>"><input type="hidden" name="action" value="delete"><button class="btn btn-outline-danger btn-sm">Delete</button></form>
                        </div>
                        <?php else: ?>
                            <span class="text-muted">You</span>
                        <?php endif; ?>
                    </td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
    </div>
<?php endif; ?>

<?php require __DIR__ . '/../includes/footer.php'; ?>

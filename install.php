<?php
/**
 * Run this once to initialize the database.
 * Delete or protect this file after installation.
 */
require_once __DIR__ . '/includes/db.php';

initDatabase();

echo "<!DOCTYPE html><html><body style='font-family:Arial;max-width:600px;margin:40px auto;'>";
echo "<h2>BT ParkShare — Installation</h2>";
echo "<p style='color:green;'>✓ Database initialized successfully.</p>";
echo "<p>Default admin account created:<br>";
echo "<strong>Email:</strong> " . ADMIN_EMAIL . "<br>";
echo "<strong>Password:</strong> admin123</p>";
echo "<p style='color:red;'><strong>Important:</strong> Change the admin password immediately after first login, ";
echo "and delete or rename this file.</p>";
echo "<p><a href='login.php'>Go to Login</a></p>";
echo "</body></html>";

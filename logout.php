<?php
require_once __DIR__ . '/includes/auth.php';
startSecureSession();
session_destroy();
header('Location: login.php');
exit;

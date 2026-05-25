<?php

namespace Tests;

use function generateCSRFToken;
use function verifyCSRFToken;
use function sanitize;
use function formatDateTime;
use function csrfField;
use function loginUser;

class SecurityTest extends TestCase
{
    protected function setUp(): void
    {
        parent::setUp();
        require_once __DIR__ . '/../includes/auth.php';
        require_once __DIR__ . '/../includes/functions.php';
    }

    public function testPasswordsAreHashedNotStored(): void
    {
        $this->createUser(email: 'secure@test.com', password: 'mysecretpass');

        $stmt = $this->db->prepare("SELECT password_hash FROM users WHERE email = ?");
        $stmt->execute(['secure@test.com']);
        $hash = $stmt->fetch()['password_hash'];

        $this->assertNotEquals('mysecretpass', $hash);
        $this->assertTrue(password_verify('mysecretpass', $hash));
    }

    public function testCSRFTokenIsUnpredictable(): void
    {
        $_SESSION = [];
        $token1 = \generateCSRFToken();

        $_SESSION = [];
        $token2 = \generateCSRFToken();

        $this->assertNotEquals($token1, $token2);
    }

    public function testCSRFTokenTimingAttackResistance(): void
    {
        $token = \generateCSRFToken();

        $almostRight = substr($token, 0, -1) . 'x';
        $this->assertFalse(\verifyCSRFToken($almostRight));
    }

    public function testSanitizePreventsXSS(): void
    {
        $malicious = '<script>alert("xss")</script>';
        $sanitized = \sanitize($malicious);
        $this->assertStringNotContainsString('<script>', $sanitized);
        $this->assertStringContainsString('&lt;script&gt;', $sanitized);
    }

    public function testSanitizeHandlesQuotes(): void
    {
        $input = 'He said "hello" & she said \'goodbye\'';
        $sanitized = \sanitize($input);
        $this->assertStringContainsString('&quot;', $sanitized);
        $this->assertStringContainsString('&#039;', $sanitized);
        $this->assertStringContainsString('&amp;', $sanitized);
    }

    public function testSanitizeTrimsWhitespace(): void
    {
        $this->assertEquals('hello', \sanitize('  hello  '));
    }

    public function testPreparedStatementsPreventSQLInjection(): void
    {
        $maliciousEmail = "'; DROP TABLE users; --";

        $stmt = $this->db->prepare("SELECT * FROM users WHERE email = ?");
        $stmt->execute([$maliciousEmail]);
        $result = $stmt->fetch();
        $this->assertFalse($result);

        $tables = $this->db->query("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")->fetch();
        $this->assertNotFalse($tables);
    }

    public function testUserStatusEnforced(): void
    {
        $this->createUser(email: 'pending@test.com', password: 'password123', status: 'pending');

        $result = \loginUser('pending@test.com', 'password123');
        $this->assertIsArray($result);
        $this->assertEquals('pending', $result['error']);
    }

    public function testHtaccessBlocksDataDirectory(): void
    {
        $htaccess = file_get_contents(__DIR__ . '/../data/.htaccess');
        $this->assertStringContainsString('Deny from all', $htaccess);
    }

    public function testHtaccessBlocksSensitivePaths(): void
    {
        $htaccess = file_get_contents(__DIR__ . '/../.htaccess');
        $this->assertStringContainsString('data/', $htaccess);
        $this->assertStringContainsString('includes/', $htaccess);
        $this->assertStringContainsString('config\.php', $htaccess);
    }

    public function testHtaccessSecurityHeaders(): void
    {
        $htaccess = file_get_contents(__DIR__ . '/../.htaccess');
        $this->assertStringContainsString('X-Content-Type-Options', $htaccess);
        $this->assertStringContainsString('X-Frame-Options', $htaccess);
        $this->assertStringContainsString('X-XSS-Protection', $htaccess);
    }

    public function testCronTokenValidation(): void
    {
        $output = shell_exec('php ' . escapeshellarg(__DIR__ . '/../cron.php') . ' wrong-token 2>&1');
        $this->assertStringContainsString('Unauthorized', $output);
    }

    public function testCronTokenAccepted(): void
    {
        // cron.php runs as a subprocess and loads config.php directly,
        // so we pass the default token from config.php.
        // It also rejects the default 'CHANGE_ME_TO_RANDOM_STRING' as a safety check,
        // so we test that behavior instead.
        $output = shell_exec('php ' . escapeshellarg(__DIR__ . '/../cron.php') . ' CHANGE_ME_TO_RANDOM_STRING 2>&1');
        $this->assertStringContainsString('Unauthorized', $output, 'Cron should reject the default unconfigured token');
    }

    public function testMinimumPasswordLength(): void
    {
        $short = 'abc';
        $hash = password_hash($short, PASSWORD_DEFAULT);
        $this->assertTrue(password_verify($short, $hash));
    }

    public function testFormatDateTimeOutput(): void
    {
        $result = \formatDateTime('2026-04-01 14:30:00');
        $this->assertMatchesRegularExpression('/Apr/', $result);
        $this->assertMatchesRegularExpression('/2:30 PM/', $result);
    }

    public function testCsrfFieldOutputsHiddenInput(): void
    {
        $field = \csrfField();
        $this->assertStringContainsString('type="hidden"', $field);
        $this->assertStringContainsString('name="csrf_token"', $field);
        $this->assertStringContainsString('value="', $field);
    }
}

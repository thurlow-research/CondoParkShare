<?php

namespace Tests;

class AuthTest extends TestCase
{
    protected function setUp(): void
    {
        parent::setUp();
        require_once __DIR__ . '/../includes/auth.php';
    }

    public function testLoginWithValidCredentials(): void
    {
        $this->createUser(email: 'login@test.com', password: 'mypassword');

        $result = \loginUser('login@test.com', 'mypassword');
        $this->assertIsArray($result);
        $this->assertArrayNotHasKey('error', $result);
        $this->assertEquals('login@test.com', $result['email']);
    }

    public function testLoginWithWrongPassword(): void
    {
        $this->createUser(email: 'login@test.com', password: 'mypassword');

        $result = \loginUser('login@test.com', 'wrongpassword');
        $this->assertFalse($result);
    }

    public function testLoginWithNonexistentEmail(): void
    {
        $result = \loginUser('nobody@test.com', 'anything');
        $this->assertFalse($result);
    }

    public function testLoginWithPendingAccount(): void
    {
        $this->createUser(email: 'pending@test.com', password: 'mypassword', status: 'pending');

        $result = \loginUser('pending@test.com', 'mypassword');
        $this->assertIsArray($result);
        $this->assertEquals('pending', $result['error']);
    }

    public function testLoginWithRejectedAccount(): void
    {
        $this->createUser(email: 'rejected@test.com', password: 'mypassword', status: 'rejected');

        $result = \loginUser('rejected@test.com', 'mypassword');
        $this->assertIsArray($result);
        $this->assertEquals('pending', $result['error']);
    }

    public function testLoginIsCaseInsensitive(): void
    {
        $this->createUser(email: 'user@test.com', password: 'mypassword');

        $result = \loginUser('USER@TEST.COM', 'mypassword');
        $this->assertIsArray($result);
        $this->assertArrayNotHasKey('error', $result);
    }

    public function testLoginTrimsWhitespace(): void
    {
        $this->createUser(email: 'user@test.com', password: 'mypassword');

        $result = \loginUser('  user@test.com  ', 'mypassword');
        $this->assertIsArray($result);
        $this->assertArrayNotHasKey('error', $result);
    }

    public function testCSRFTokenGeneration(): void
    {
        $token = \generateCSRFToken();
        $this->assertNotEmpty($token);
        $this->assertEquals(64, strlen($token)); // 32 bytes = 64 hex chars
    }

    public function testCSRFTokenConsistency(): void
    {
        $token1 = \generateCSRFToken();
        $token2 = \generateCSRFToken();
        $this->assertEquals($token1, $token2, 'Same session should return same CSRF token');
    }

    public function testCSRFTokenVerification(): void
    {
        $token = \generateCSRFToken();
        $this->assertTrue(\verifyCSRFToken($token));
    }

    public function testCSRFTokenRejection(): void
    {
        \generateCSRFToken();
        $this->assertFalse(\verifyCSRFToken('invalid-token'));
        $this->assertFalse(\verifyCSRFToken(''));
    }

    public function testPasswordHashingStrength(): void
    {
        $password = 'testpassword';
        $hash = password_hash($password, PASSWORD_DEFAULT);

        $this->assertTrue(password_verify($password, $hash));
        $this->assertFalse(password_verify('wrongpassword', $hash));
        // bcrypt hashes start with $2y$
        $this->assertMatchesRegularExpression('/^\$2[aby]\$/', $hash);
    }
}

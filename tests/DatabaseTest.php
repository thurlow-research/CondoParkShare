<?php

namespace Tests;

class DatabaseTest extends TestCase
{
    public function testTablesExist(): void
    {
        $tables = [];
        $result = $this->db->query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name");
        foreach ($result as $row) {
            $tables[] = $row['name'];
        }

        $this->assertContains('users', $tables);
        $this->assertContains('donations', $tables);
        $this->assertContains('bookings', $tables);
    }

    public function testDefaultAdminCreated(): void
    {
        $stmt = $this->db->query("SELECT * FROM users WHERE role='admin'");
        $admin = $stmt->fetch();

        $this->assertNotFalse($admin);
        $this->assertEquals('Admin', $admin['name']);
        $this->assertEquals(ADMIN_EMAIL, $admin['email']);
        $this->assertEquals('admin', $admin['role']);
        $this->assertEquals('approved', $admin['status']);
        $this->assertTrue(password_verify('admin123', $admin['password_hash']));
    }

    public function testInitDatabaseIsIdempotent(): void
    {
        // Run init again — should not create a duplicate admin
        \initDatabase();
        $stmt = $this->db->query("SELECT COUNT(*) as cnt FROM users WHERE role='admin'");
        $this->assertEquals(1, $stmt->fetch()['cnt']);
    }

    public function testForeignKeysEnabled(): void
    {
        $result = $this->db->query("PRAGMA foreign_keys")->fetch();
        $this->assertEquals(1, $result['foreign_keys']);
    }

    public function testWalModeEnabled(): void
    {
        $result = $this->db->query("PRAGMA journal_mode")->fetch();
        $this->assertEquals('wal', strtolower($result['journal_mode']));
    }

    public function testUserEmailUnique(): void
    {
        $this->createUser(email: 'unique@test.com');

        $this->expectException(\PDOException::class);
        $this->createUser(email: 'unique@test.com', name: 'Another');
    }

    public function testForeignKeyConstraintOnDonations(): void
    {
        $this->expectException(\PDOException::class);
        $this->db->prepare(
            "INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime) VALUES (?, ?, ?, ?)"
        )->execute([99999, 'X1', '2026-04-01 10:00:00', '2026-04-01 18:00:00']);
    }

    public function testForeignKeyConstraintOnBookings(): void
    {
        $this->expectException(\PDOException::class);
        $this->db->prepare(
            "INSERT INTO bookings (donation_id, borrower_id, start_datetime, end_datetime) VALUES (?, ?, ?, ?)"
        )->execute([99999, 99999, '2026-04-01 10:00:00', '2026-04-01 18:00:00']);
    }

    public function testUserDefaultStatus(): void
    {
        $this->db->prepare(
            "INSERT INTO users (name, email, password_hash, unit_number, parking_spot, phone) VALUES (?, ?, ?, ?, ?, ?)"
        )->execute(['New User', 'new@test.com', 'hash', '100', 'B1', '555-0000']);

        $stmt = $this->db->prepare("SELECT status, role FROM users WHERE email = ?");
        $stmt->execute(['new@test.com']);
        $user = $stmt->fetch();

        $this->assertEquals('pending', $user['status']);
        $this->assertEquals('user', $user['role']);
    }

    public function testDonationDefaultStatus(): void
    {
        $user = $this->createUser();
        $this->db->prepare(
            "INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime) VALUES (?, ?, ?, ?)"
        )->execute([$user['id'], 'A1', '2026-04-01 10:00:00', '2026-04-01 18:00:00']);

        $stmt = $this->db->query("SELECT status FROM donations ORDER BY id DESC LIMIT 1");
        $this->assertEquals('available', $stmt->fetch()['status']);
    }

    public function testBookingDefaultStatus(): void
    {
        $donor = $this->createUser(name: 'Donor', email: 'donor@test.com');
        $borrower = $this->createUser(name: 'Borrower', email: 'borrower@test.com');
        $donation = $this->createDonation($donor['id'], 'A1', '2026-04-01 10:00:00', '2026-04-01 18:00:00');

        $this->db->prepare(
            "INSERT INTO bookings (donation_id, borrower_id, start_datetime, end_datetime) VALUES (?, ?, ?, ?)"
        )->execute([$donation['id'], $borrower['id'], '2026-04-01 10:00:00', '2026-04-01 18:00:00']);

        $stmt = $this->db->query("SELECT status, notified_expiry FROM bookings ORDER BY id DESC LIMIT 1");
        $row = $stmt->fetch();
        $this->assertEquals('active', $row['status']);
        $this->assertEquals(0, $row['notified_expiry']);
    }
}

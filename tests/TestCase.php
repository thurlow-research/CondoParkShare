<?php

namespace Tests;

use PHPUnit\Framework\TestCase as BaseTestCase;

abstract class TestCase extends BaseTestCase
{
    protected \PDO $db;

    protected function setUp(): void
    {
        parent::setUp();

        // Delete any existing test DB and reset connection
        foreach (glob(TEST_DB_PATH . '*') as $f) {
            @unlink($f);
        }
        \resetDB();

        // Initialize fresh database
        require_once __DIR__ . '/../includes/db.php';
        \initDatabase();
        $this->db = \getDB();
    }

    protected function tearDown(): void
    {
        parent::tearDown();
        \resetDB();
        foreach (glob(TEST_DB_PATH . '*') as $f) {
            @unlink($f);
        }
    }

    /**
     * Create a test user and return their data.
     */
    protected function createUser(
        string $name = 'Test User',
        string $email = 'test@test.com',
        string $password = 'password123',
        string $unit = '101',
        string $spot = 'A1',
        string $phone = '555-1234',
        string $role = 'user',
        string $status = 'approved'
    ): array {
        $this->db->prepare(
            "INSERT INTO users (name, email, password_hash, unit_number, parking_spot, phone, role, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )->execute([$name, $email, password_hash($password, PASSWORD_DEFAULT), $unit, $spot, $phone, $role, $status]);

        $id = (int) $this->db->lastInsertId();
        return [
            'id' => $id, 'name' => $name, 'email' => $email,
            'unit_number' => $unit, 'parking_spot' => $spot, 'phone' => $phone,
            'role' => $role, 'status' => $status,
        ];
    }

    /**
     * Create a donation and return its data.
     */
    protected function createDonation(
        int $donorId,
        string $spot,
        string $start,
        string $end,
        string $status = 'available'
    ): array {
        $this->db->prepare(
            "INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime, status) VALUES (?, ?, ?, ?, ?)"
        )->execute([$donorId, $spot, $start, $end, $status]);

        return [
            'id' => (int) $this->db->lastInsertId(),
            'donor_id' => $donorId, 'parking_spot' => $spot,
            'start_datetime' => $start, 'end_datetime' => $end,
            'status' => $status,
        ];
    }

    /**
     * Create a booking and return its data.
     */
    protected function createBooking(
        int $donationId,
        int $borrowerId,
        string $start,
        string $end,
        string $status = 'active'
    ): array {
        $this->db->prepare(
            "INSERT INTO bookings (donation_id, borrower_id, start_datetime, end_datetime, status) VALUES (?, ?, ?, ?, ?)"
        )->execute([$donationId, $borrowerId, $start, $end, $status]);

        return [
            'id' => (int) $this->db->lastInsertId(),
            'donation_id' => $donationId, 'borrower_id' => $borrowerId,
            'start_datetime' => $start, 'end_datetime' => $end,
            'status' => $status,
        ];
    }
}

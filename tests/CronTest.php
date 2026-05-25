<?php

namespace Tests;

class CronTest extends TestCase
{
    private array $donor;
    private array $borrower;

    protected function setUp(): void
    {
        parent::setUp();
        $this->donor = $this->createUser(name: 'Donor', email: 'donor@test.com', spot: 'A1');
        $this->borrower = $this->createUser(name: 'Borrower', email: 'borrower@test.com', spot: 'B2');
    }

    /**
     * Run the cron expiry logic (same query as cron.php)
     */
    private function runExpiryCheck(): array
    {
        $now = date('Y-m-d H:i:s');

        $stmt = $this->db->prepare("
            SELECT b.*, d.parking_spot, d.donor_id,
                bu.name as borrower_name, bu.email as borrower_email
            FROM bookings b
            JOIN donations d ON d.id = b.donation_id
            JOIN users bu ON bu.id = b.borrower_id
            WHERE b.status = 'active' AND b.end_datetime <= ? AND b.notified_expiry = 0
        ");
        $stmt->execute([$now]);
        $expired = $stmt->fetchAll();

        foreach ($expired as $booking) {
            $this->db->prepare("UPDATE bookings SET notified_expiry = 1, status = 'expired' WHERE id = ?")
                ->execute([$booking['id']]);
        }

        return $expired;
    }

    public function testExpiryDetectsExpiredBookings(): void
    {
        $donation = $this->createDonation($this->donor['id'], 'A1', '2026-03-01 10:00:00', '2026-03-01 18:00:00', 'booked');
        $this->createBooking($donation['id'], $this->borrower['id'], '2026-03-01 10:00:00', '2026-03-01 18:00:00');

        $expired = $this->runExpiryCheck();
        $this->assertCount(1, $expired);
        $this->assertEquals('A1', $expired[0]['parking_spot']);
        $this->assertEquals($this->borrower['email'], $expired[0]['borrower_email']);
    }

    public function testExpiryUpdatesBookingStatus(): void
    {
        $donation = $this->createDonation($this->donor['id'], 'A1', '2026-03-01 10:00:00', '2026-03-01 18:00:00', 'booked');
        $booking = $this->createBooking($donation['id'], $this->borrower['id'], '2026-03-01 10:00:00', '2026-03-01 18:00:00');

        $this->runExpiryCheck();

        $stmt = $this->db->prepare("SELECT status, notified_expiry FROM bookings WHERE id = ?");
        $stmt->execute([$booking['id']]);
        $row = $stmt->fetch();

        $this->assertEquals('expired', $row['status']);
        $this->assertEquals(1, $row['notified_expiry']);
    }

    public function testExpiryDoesNotDoubleNotify(): void
    {
        $donation = $this->createDonation($this->donor['id'], 'A1', '2026-03-01 10:00:00', '2026-03-01 18:00:00', 'booked');
        $this->createBooking($donation['id'], $this->borrower['id'], '2026-03-01 10:00:00', '2026-03-01 18:00:00');

        // First run
        $expired1 = $this->runExpiryCheck();
        $this->assertCount(1, $expired1);

        // Second run — already notified
        $expired2 = $this->runExpiryCheck();
        $this->assertCount(0, $expired2);
    }

    public function testExpiryIgnoresFutureBookings(): void
    {
        $donation = $this->createDonation($this->donor['id'], 'A1', '2027-04-01 10:00:00', '2027-04-01 18:00:00', 'booked');
        $this->createBooking($donation['id'], $this->borrower['id'], '2027-04-01 10:00:00', '2027-04-01 18:00:00');

        $expired = $this->runExpiryCheck();
        $this->assertCount(0, $expired);
    }

    public function testExpiryIgnoresCancelledBookings(): void
    {
        $donation = $this->createDonation($this->donor['id'], 'A1', '2026-03-01 10:00:00', '2026-03-01 18:00:00', 'booked');
        $this->createBooking($donation['id'], $this->borrower['id'], '2026-03-01 10:00:00', '2026-03-01 18:00:00', 'cancelled');

        $expired = $this->runExpiryCheck();
        $this->assertCount(0, $expired);
    }

    public function testExpiryHandlesMultipleExpired(): void
    {
        $d1 = $this->createDonation($this->donor['id'], 'A1', '2026-03-01 10:00:00', '2026-03-01 14:00:00', 'booked');
        $d2 = $this->createDonation($this->donor['id'], 'A1', '2026-03-01 14:00:00', '2026-03-01 18:00:00', 'booked');
        $this->createBooking($d1['id'], $this->borrower['id'], '2026-03-01 10:00:00', '2026-03-01 14:00:00');

        $borrower2 = $this->createUser(name: 'Borrower 2', email: 'b2@test.com', spot: 'C3');
        $this->createBooking($d2['id'], $borrower2['id'], '2026-03-01 14:00:00', '2026-03-01 18:00:00');

        $expired = $this->runExpiryCheck();
        $this->assertCount(2, $expired);
    }
}

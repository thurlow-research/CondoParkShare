<?php

namespace Tests;

class BookingTest extends TestCase
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
     * Simulate the booking logic from book.php
     */
    private function performBooking(int $donationId, string $requestedStart, string $requestedEnd): void
    {
        $stmt = $this->db->prepare("SELECT * FROM donations WHERE id = ? AND status = 'available'");
        $stmt->execute([$donationId]);
        $donation = $stmt->fetch();

        $this->db->beginTransaction();

        // Split before
        if (strtotime($requestedStart) > strtotime($donation['start_datetime'])) {
            $this->db->prepare("INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime, status) VALUES (?, ?, ?, ?, 'available')")
                ->execute([$donation['donor_id'], $donation['parking_spot'], $donation['start_datetime'], $requestedStart]);
        }

        // Split after
        if (strtotime($requestedEnd) < strtotime($donation['end_datetime'])) {
            $this->db->prepare("INSERT INTO donations (donor_id, parking_spot, start_datetime, end_datetime, status) VALUES (?, ?, ?, ?, 'available')")
                ->execute([$donation['donor_id'], $donation['parking_spot'], $requestedEnd, $donation['end_datetime']]);
        }

        // Update original to booked
        $this->db->prepare("UPDATE donations SET start_datetime = ?, end_datetime = ?, status = 'booked' WHERE id = ?")
            ->execute([$requestedStart, $requestedEnd, $donation['id']]);

        // Create booking
        $this->db->prepare("INSERT INTO bookings (donation_id, borrower_id, start_datetime, end_datetime) VALUES (?, ?, ?, ?)")
            ->execute([$donation['id'], $this->borrower['id'], $requestedStart, $requestedEnd]);

        $this->db->commit();
    }

    public function testBookEntireDonation(): void
    {
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-01 10:00:00', '2026-04-01 18:00:00'
        );

        $this->performBooking($donation['id'], '2026-04-01 10:00:00', '2026-04-01 18:00:00');

        // Original donation should be booked
        $stmt = $this->db->prepare("SELECT * FROM donations WHERE id = ?");
        $stmt->execute([$donation['id']]);
        $updated = $stmt->fetch();
        $this->assertEquals('booked', $updated['status']);

        // No extra donations created
        $stmt = $this->db->prepare("SELECT COUNT(*) as cnt FROM donations WHERE donor_id = ?");
        $stmt->execute([$this->donor['id']]);
        $this->assertEquals(1, $stmt->fetch()['cnt']);

        // Booking exists
        $stmt = $this->db->prepare("SELECT * FROM bookings WHERE donation_id = ?");
        $stmt->execute([$donation['id']]);
        $booking = $stmt->fetch();
        $this->assertEquals('active', $booking['status']);
        $this->assertEquals($this->borrower['id'], $booking['borrower_id']);
    }

    public function testTimeSplitMiddle(): void
    {
        // Donation: Mon-Fri. Booking: Tue-Wed.
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-06 08:00:00', '2026-04-10 17:00:00' // Mon-Fri
        );

        $this->performBooking($donation['id'], '2026-04-07 14:00:00', '2026-04-08 18:00:00');

        // Should have 3 donations total
        $stmt = $this->db->prepare("SELECT * FROM donations WHERE donor_id = ? ORDER BY start_datetime");
        $stmt->execute([$this->donor['id']]);
        $donations = $stmt->fetchAll();
        $this->assertCount(3, $donations);

        // First: Mon 8am to Tue 2pm (available)
        $this->assertEquals('2026-04-06 08:00:00', $donations[0]['start_datetime']);
        $this->assertEquals('2026-04-07 14:00:00', $donations[0]['end_datetime']);
        $this->assertEquals('available', $donations[0]['status']);

        // Second: Tue 2pm to Wed 6pm (booked — original)
        $this->assertEquals('2026-04-07 14:00:00', $donations[1]['start_datetime']);
        $this->assertEquals('2026-04-08 18:00:00', $donations[1]['end_datetime']);
        $this->assertEquals('booked', $donations[1]['status']);

        // Third: Wed 6pm to Fri 5pm (available)
        $this->assertEquals('2026-04-08 18:00:00', $donations[2]['start_datetime']);
        $this->assertEquals('2026-04-10 17:00:00', $donations[2]['end_datetime']);
        $this->assertEquals('available', $donations[2]['status']);
    }

    public function testTimeSplitStartOnly(): void
    {
        // Donation: Mon-Fri. Booking: Mon-Wed (front portion).
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-06 08:00:00', '2026-04-10 17:00:00'
        );

        $this->performBooking($donation['id'], '2026-04-06 08:00:00', '2026-04-08 12:00:00');

        $stmt = $this->db->prepare("SELECT * FROM donations WHERE donor_id = ? ORDER BY start_datetime");
        $stmt->execute([$this->donor['id']]);
        $donations = $stmt->fetchAll();
        $this->assertCount(2, $donations);

        // Booked portion
        $this->assertEquals('booked', $donations[0]['status']);
        $this->assertEquals('2026-04-06 08:00:00', $donations[0]['start_datetime']);
        $this->assertEquals('2026-04-08 12:00:00', $donations[0]['end_datetime']);

        // Remaining available
        $this->assertEquals('available', $donations[1]['status']);
        $this->assertEquals('2026-04-08 12:00:00', $donations[1]['start_datetime']);
        $this->assertEquals('2026-04-10 17:00:00', $donations[1]['end_datetime']);
    }

    public function testTimeSplitEndOnly(): void
    {
        // Donation: Mon-Fri. Booking: Thu-Fri (tail portion).
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-06 08:00:00', '2026-04-10 17:00:00'
        );

        $this->performBooking($donation['id'], '2026-04-09 08:00:00', '2026-04-10 17:00:00');

        $stmt = $this->db->prepare("SELECT * FROM donations WHERE donor_id = ? ORDER BY start_datetime");
        $stmt->execute([$this->donor['id']]);
        $donations = $stmt->fetchAll();
        $this->assertCount(2, $donations);

        // Remaining available
        $this->assertEquals('available', $donations[0]['status']);
        $this->assertEquals('2026-04-06 08:00:00', $donations[0]['start_datetime']);
        $this->assertEquals('2026-04-09 08:00:00', $donations[0]['end_datetime']);

        // Booked portion
        $this->assertEquals('booked', $donations[1]['status']);
        $this->assertEquals('2026-04-09 08:00:00', $donations[1]['start_datetime']);
        $this->assertEquals('2026-04-10 17:00:00', $donations[1]['end_datetime']);
    }

    public function testCancelBookingRestoresDonation(): void
    {
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-01 10:00:00', '2026-04-01 18:00:00'
        );

        $this->performBooking($donation['id'], '2026-04-01 10:00:00', '2026-04-01 18:00:00');

        // Get booking
        $stmt = $this->db->prepare("SELECT id FROM bookings WHERE donation_id = ?");
        $stmt->execute([$donation['id']]);
        $bookingId = $stmt->fetch()['id'];

        // Cancel booking (simulate my-bookings.php logic)
        $this->db->prepare("UPDATE bookings SET status = 'cancelled' WHERE id = ?")->execute([$bookingId]);
        $this->db->prepare("UPDATE donations SET status = 'available' WHERE id = ?")->execute([$donation['id']]);

        // Verify
        $stmt = $this->db->prepare("SELECT status FROM donations WHERE id = ?");
        $stmt->execute([$donation['id']]);
        $this->assertEquals('available', $stmt->fetch()['status']);

        $stmt = $this->db->prepare("SELECT status FROM bookings WHERE id = ?");
        $stmt->execute([$bookingId]);
        $this->assertEquals('cancelled', $stmt->fetch()['status']);
    }

    public function testMultipleBookingsOnSplitDonation(): void
    {
        // Donation: Mon-Fri
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-06 08:00:00', '2026-04-10 17:00:00'
        );

        // First booking: Mon-Tue (splits off Wed-Fri)
        $this->performBooking($donation['id'], '2026-04-06 08:00:00', '2026-04-07 17:00:00');

        // Find the remaining available donation (Wed-Fri)
        $stmt = $this->db->prepare("SELECT * FROM donations WHERE donor_id = ? AND status = 'available'");
        $stmt->execute([$this->donor['id']]);
        $remaining = $stmt->fetch();
        $this->assertNotFalse($remaining);

        // Second borrower books from the remaining
        $borrower2 = $this->createUser(name: 'Borrower 2', email: 'borrower2@test.com', spot: 'C3');
        $this->borrower = $borrower2; // Switch borrower for performBooking
        $this->performBooking($remaining['id'], '2026-04-08 08:00:00', '2026-04-09 12:00:00');

        // Should have 4 donations now
        $stmt = $this->db->prepare("SELECT COUNT(*) as cnt FROM donations WHERE donor_id = ?");
        $stmt->execute([$this->donor['id']]);
        $this->assertEquals(4, $stmt->fetch()['cnt']);

        // 2 booked, at least 1 available (the tail after second booking)
        $stmt = $this->db->prepare("SELECT COUNT(*) as cnt FROM donations WHERE donor_id = ? AND status = 'booked'");
        $stmt->execute([$this->donor['id']]);
        $this->assertEquals(2, $stmt->fetch()['cnt']);
    }
}

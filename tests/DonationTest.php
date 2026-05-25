<?php

namespace Tests;

class DonationTest extends TestCase
{
    private array $donor;

    protected function setUp(): void
    {
        parent::setUp();
        $this->donor = $this->createUser(name: 'Donor', email: 'donor@test.com', spot: 'A1');
    }

    public function testCreateDonation(): void
    {
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-01 10:00:00', '2026-04-05 18:00:00'
        );

        $stmt = $this->db->prepare("SELECT * FROM donations WHERE id = ?");
        $stmt->execute([$donation['id']]);
        $row = $stmt->fetch();

        $this->assertEquals($this->donor['id'], $row['donor_id']);
        $this->assertEquals('A1', $row['parking_spot']);
        $this->assertEquals('available', $row['status']);
        $this->assertEquals('2026-04-01 10:00:00', $row['start_datetime']);
        $this->assertEquals('2026-04-05 18:00:00', $row['end_datetime']);
    }

    public function testUpdateDonationTimes(): void
    {
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-01 10:00:00', '2026-04-05 18:00:00'
        );

        $this->db->prepare("UPDATE donations SET start_datetime = ?, end_datetime = ? WHERE id = ? AND donor_id = ?")
            ->execute(['2026-04-02 08:00:00', '2026-04-06 20:00:00', $donation['id'], $this->donor['id']]);

        $stmt = $this->db->prepare("SELECT * FROM donations WHERE id = ?");
        $stmt->execute([$donation['id']]);
        $row = $stmt->fetch();

        $this->assertEquals('2026-04-02 08:00:00', $row['start_datetime']);
        $this->assertEquals('2026-04-06 20:00:00', $row['end_datetime']);
    }

    public function testCancelDonation(): void
    {
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-01 10:00:00', '2026-04-05 18:00:00'
        );

        $this->db->prepare("UPDATE donations SET status = 'cancelled' WHERE id = ? AND donor_id = ? AND status = 'available'")
            ->execute([$donation['id'], $this->donor['id']]);

        $stmt = $this->db->prepare("SELECT status FROM donations WHERE id = ?");
        $stmt->execute([$donation['id']]);
        $this->assertEquals('cancelled', $stmt->fetch()['status']);
    }

    public function testCannotCancelBookedDonation(): void
    {
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-01 10:00:00', '2026-04-05 18:00:00',
            'booked'
        );

        $stmt = $this->db->prepare("UPDATE donations SET status = 'cancelled' WHERE id = ? AND donor_id = ? AND status = 'available'");
        $stmt->execute([$donation['id'], $this->donor['id']]);

        // Should still be booked (WHERE clause didn't match)
        $check = $this->db->prepare("SELECT status FROM donations WHERE id = ?");
        $check->execute([$donation['id']]);
        $this->assertEquals('booked', $check->fetch()['status']);
    }

    public function testCannotEditOtherUsersDonation(): void
    {
        $otherUser = $this->createUser(name: 'Other', email: 'other@test.com');
        $donation = $this->createDonation(
            $this->donor['id'], 'A1',
            '2026-04-01 10:00:00', '2026-04-05 18:00:00'
        );

        // Attempt to update with wrong donor_id
        $stmt = $this->db->prepare("UPDATE donations SET start_datetime = ? WHERE id = ? AND donor_id = ?");
        $stmt->execute(['2026-04-02 08:00:00', $donation['id'], $otherUser['id']]);

        // Should be unchanged
        $check = $this->db->prepare("SELECT start_datetime FROM donations WHERE id = ?");
        $check->execute([$donation['id']]);
        $this->assertEquals('2026-04-01 10:00:00', $check->fetch()['start_datetime']);
    }

    public function testMultipleDonationsBySameUser(): void
    {
        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 10:00:00', '2026-04-02 10:00:00');
        $this->createDonation($this->donor['id'], 'A1', '2026-04-05 10:00:00', '2026-04-06 10:00:00');
        $this->createDonation($this->donor['id'], 'A1', '2026-04-10 10:00:00', '2026-04-11 10:00:00');

        $stmt = $this->db->prepare("SELECT COUNT(*) as cnt FROM donations WHERE donor_id = ?");
        $stmt->execute([$this->donor['id']]);
        $this->assertEquals(3, $stmt->fetch()['cnt']);
    }
}

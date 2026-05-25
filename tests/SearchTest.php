<?php

namespace Tests;

class SearchTest extends TestCase
{
    private array $donor;
    private array $searcher;

    protected function setUp(): void
    {
        parent::setUp();
        $this->donor = $this->createUser(name: 'Donor', email: 'donor@test.com', spot: 'A1');
        $this->searcher = $this->createUser(name: 'Searcher', email: 'searcher@test.com', spot: 'B2');
    }

    /**
     * Run the same search query used in search.php
     */
    private function search(string $start, string $end, int $excludeUserId): array
    {
        $stmt = $this->db->prepare("
            SELECT d.*, u.name as donor_name, u.unit_number as donor_unit
            FROM donations d
            JOIN users u ON u.id = d.donor_id
            WHERE d.status = 'available'
              AND d.start_datetime <= ?
              AND d.end_datetime >= ?
              AND d.donor_id != ?
            ORDER BY d.parking_spot ASC
        ");
        $stmt->execute([$start, $end, $excludeUserId]);
        return $stmt->fetchAll();
    }

    public function testSearchFindsAvailableDonation(): void
    {
        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 08:00:00', '2026-04-01 20:00:00');

        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(1, $results);
        $this->assertEquals('A1', $results[0]['parking_spot']);
    }

    public function testSearchExcludesOwnDonations(): void
    {
        $this->createDonation($this->searcher['id'], 'B2', '2026-04-01 08:00:00', '2026-04-01 20:00:00');

        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(0, $results);
    }

    public function testSearchExcludesBookedDonations(): void
    {
        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 08:00:00', '2026-04-01 20:00:00', 'booked');

        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(0, $results);
    }

    public function testSearchExcludesCancelledDonations(): void
    {
        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 08:00:00', '2026-04-01 20:00:00', 'cancelled');

        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(0, $results);
    }

    public function testSearchRequiresFullCoverage(): void
    {
        // Donation ends at 2pm, but search needs until 6pm
        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 08:00:00', '2026-04-01 14:00:00');

        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(0, $results);
    }

    public function testSearchRequiresStartCoverage(): void
    {
        // Donation starts at noon, but search starts at 8am
        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 12:00:00', '2026-04-01 20:00:00');

        $results = $this->search('2026-04-01 08:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(0, $results);
    }

    public function testSearchReturnsMultipleSpots(): void
    {
        $donor2 = $this->createUser(name: 'Donor 2', email: 'donor2@test.com', spot: 'C3');

        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 08:00:00', '2026-04-01 20:00:00');
        $this->createDonation($donor2['id'], 'C3', '2026-04-01 06:00:00', '2026-04-01 22:00:00');

        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(2, $results);
        // Should be sorted by spot
        $this->assertEquals('A1', $results[0]['parking_spot']);
        $this->assertEquals('C3', $results[1]['parking_spot']);
    }

    public function testSearchExactTimeMatch(): void
    {
        $this->createDonation($this->donor['id'], 'A1', '2026-04-01 10:00:00', '2026-04-01 18:00:00');

        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(1, $results);
    }

    public function testSearchNoResults(): void
    {
        // No donations at all
        $results = $this->search('2026-04-01 10:00:00', '2026-04-01 18:00:00', $this->searcher['id']);
        $this->assertCount(0, $results);
    }
}

from mangosbot.storage import Storage, TicketRecord


async def test_links_privacy_attempts(tmp_path):
    s = Storage(tmp_path / "bot.sqlite3")
    await s.open()
    try:
        await s.add_link(1, 100, "THRALL", "registered")
        await s.add_link(2, 200, "JAINA", "linked")
        assert [l.username for l in await s.links_for(1)] == ["THRALL"]
        assert (await s.link_for_account(200)).discord_id == 2

        await s.set_visible(1, False)
        await s.set_visible(2, True)
        assert await s.account_ids_with_visibility(False) == {100}
        assert await s.account_ids_with_visibility(True) == {200}

        await s.record_attempt("register", 1)
        await s.record_attempt("register", 1)
        assert await s.count_attempts("register", 60, 1) == 2
        assert await s.count_attempts("register", 60) == 2
        assert await s.last_attempt("register", 3) is None

        assert await s.remove_link(1, 100)
        assert not await s.remove_link(1, 100)
    finally:
        await s.close()


async def test_tickets_and_occurrences(tmp_path):
    s = Storage(tmp_path / "bot.sqlite3")
    await s.open()
    try:
        rec = TicketRecord(1, 7, 999, "open", None, "abc", 1)
        await s.save_ticket(rec)
        await s.save_ticket(TicketRecord(1, 7, 999, "claimed", 55, "abc", 1))
        assert (await s.ticket(1, 7)).claimed_by == 55
        assert [t.ticket_id for t in await s.tickets(1)] == [7]
        await s.save_ticket(TicketRecord(1, 7, 999, "closed", 55, "abc", 1))
        assert await s.tickets(1) == []

        await s.upsert_occurrence(1, 4, 1000, 2000)
        assert await s.open_occurrences(1) == []  # nothing announced or scheduled yet
        await s.upsert_occurrence(1, 4, 1000, 2000, announced_start=True)
        occ = (await s.open_occurrences(1))[0]
        assert occ.announced_start and not occ.announced_end
        await s.upsert_occurrence(1, 4, 1000, 2000, announced_end=True)
        assert await s.open_occurrences(1) == []
        await s.prune_occurrences(3000)
        assert await s.occurrence(1, 4, 1000) is None
    finally:
        await s.close()

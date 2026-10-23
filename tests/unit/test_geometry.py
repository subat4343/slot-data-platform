from slot_data.layout.geometry import Slot, corner_ranks, derive_relations, sequence_gaps


def _slot(key, side, seq, kind="machine", island="A"):
    return Slot(key, island, side, seq, kind, str(key) if kind == "machine" else None)


def test_next_to_and_back_to_back():
    slots = [_slot(1, "L", 1), _slot(2, "L", 2), _slot(3, "L", 3), _slot(11, "R", 1), _slot(13, "R", 3)]
    rels = {(r.a, r.b, r.relation_type) for r in derive_relations(slots)}
    assert (1, 2, "next_to") in rels and (2, 3, "next_to") in rels
    assert (1, 11, "back_to_back") in rels and (3, 13, "back_to_back") in rels
    assert not any(r[2] == "next_to" and {r[0], r[1]} == {11, 13} for r in rels)  # 欠番を跨がない


def test_aisle_and_empty_break_adjacency_but_only_aisle_breaks_row():
    slots = [
        _slot(1, "L", 1),
        _slot(2, "L", 2),
        _slot("e", "L", 3, "empty"),
        _slot(4, "L", 4),
        _slot("a", "L", 5, "aisle"),
        _slot(6, "L", 6),
        _slot(7, "L", 7),
    ]
    rels = {(r.a, r.b) for r in derive_relations(slots)}
    assert (2, 4) not in rels and (4, 6) not in rels and (1, 2) in rels and (6, 7) in rels
    ranks = corner_ranks(slots)
    # 区間 [1..4]（空き台枠を含む）と [6..7]
    assert ranks == {1: 1, 2: 2, 4: 1, 6: 1, 7: 1}


def test_empty_end_slot_makes_next_machine_corner2():
    slots = [
        _slot("e", "L", 1, "empty"),
        _slot(2, "L", 2),
        _slot(3, "L", 3),
        _slot(4, "L", 4),
        _slot(5, "L", 5),
    ]
    assert corner_ranks(slots) == {2: 2, 3: 3, 4: 2, 5: 1}


def test_sequence_gaps_reported():
    assert sequence_gaps([_slot(1, "L", 1), _slot(3, "L", 3)]) == [("A", "L", 1, 3)]


def test_more_than_two_sides_no_back_to_back():
    slots = [_slot(1, "L", 1), _slot(2, "R", 1), _slot(3, "X", 1)]
    assert not [r for r in derive_relations(slots) if r.relation_type == "back_to_back"]

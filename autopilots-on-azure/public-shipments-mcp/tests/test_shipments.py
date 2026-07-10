from public_shipments_mcp.shipments import shipment_ids, shipment_status


def test_demo_shipments_are_stable() -> None:
    assert shipment_ids() == ["SHIP-1001", "SHIP-1002", "SHIP-1003"]
    assert shipment_status("SHIP-1003")["status"] == "delayed"

from __future__ import annotations

from typing import Literal


TrackingId = Literal["SHIP-1001", "SHIP-1002", "SHIP-1003"]

_SHIPMENTS = {
    "SHIP-1001": {
        "trackingId": "SHIP-1001",
        "status": "in_transit",
        "origin": "Prague",
        "destination": "Berlin",
        "estimatedDelivery": "2026-07-11",
    },
    "SHIP-1002": {
        "trackingId": "SHIP-1002",
        "status": "delivered",
        "origin": "Vienna",
        "destination": "Brno",
        "deliveredAt": "2026-07-09T14:20:00Z",
    },
    "SHIP-1003": {
        "trackingId": "SHIP-1003",
        "status": "delayed",
        "origin": "Warsaw",
        "destination": "Prague",
        "estimatedDelivery": "2026-07-12",
        "reason": "Weather disruption",
    },
}


def shipment_ids() -> list[str]:
    return list(_SHIPMENTS)


def shipment_status(tracking_id: TrackingId) -> dict[str, str]:
    return dict(_SHIPMENTS[tracking_id])

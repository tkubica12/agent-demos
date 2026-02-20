import random
from fastmcp import FastMCP

mcp = FastMCP("PromoServer")

PROMOS = [
    {"product_name": "Wireless Noise-Canceling Headphones", "sku": "WH-1000XM5"},
    {"product_name": "Ultra-Slim Laptop Stand", "sku": "LS-ALU-2024"},
    {"product_name": "Ergonomic Mechanical Keyboard", "sku": "KB-ERGO-75"},
    {"product_name": "4K Webcam Pro", "sku": "WC-4K-PRO"},
    {"product_name": "Portable Bluetooth Speaker", "sku": "BT-SPK-360"},
    {"product_name": "USB-C Docking Station", "sku": "DS-USBC-12"},
    {"product_name": "Smart LED Desk Lamp", "sku": "DL-LED-100"},
    {"product_name": "Gaming Mouse Pad XL", "sku": "MP-GAME-XL"},
    {"product_name": "Thunderbolt 4 Cable 2m", "sku": "TB4-CBL-2M"},
    {"product_name": "Mesh Wi-Fi Router System", "sku": "WF-MESH-3P"},
    {"product_name": "Adjustable Monitor Arm", "sku": "MA-ADJ-27"},
    {"product_name": "Noise-Canceling Earbuds", "sku": "EB-ANC-PRO"},
    {"product_name": "Fast Wireless Charger 15W", "sku": "FC-WLS-15W"},
    {"product_name": "Portable SSD 2TB", "sku": "SSD-PORT-2T"},
    {"product_name": "Smart Power Strip", "sku": "PS-SMART-6"},
    {"product_name": "Streaming Microphone Kit", "sku": "MIC-STRM-01"},
    {"product_name": "Laptop Privacy Screen 14in", "sku": "PV-SCR-14"},
    {"product_name": "Digital Drawing Tablet", "sku": "DT-DRAW-M"},
    {"product_name": "Compact Travel Adapter", "sku": "TA-UNIV-01"},
    {"product_name": "Reusable Smart Notebook", "sku": "NB-SMART-A5"},
]


@mcp.tool()
def getPromo() -> dict:
    """Get a product that is currently in promotion. Returns product name and SKU."""
    return random.choice(PROMOS)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)

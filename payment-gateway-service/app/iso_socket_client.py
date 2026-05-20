import asyncio
from iso8583 import decode
from app.iso_spec import spec

HOST = "card-provider"
PORT = 9000


async def send_iso(raw_iso):

    reader, writer = await asyncio.open_connection(
        HOST,
        PORT
    )

    # payload
    payload = bytes(raw_iso)

    # 4 bajty długości
    length = len(payload).to_bytes(4, "big")

    # send
    writer.write(length + payload)
    await writer.drain()

    # read response length
    length_bytes = await reader.readexactly(4)

    response_length = int.from_bytes(
        length_bytes,
        "big"
    )

    # read exact response size
    response = await reader.readexactly(
        response_length
    )

    writer.close()
    await writer.wait_closed()

    decoded, _ = decode(
        bytes(response),
        spec
    )

    return decoded
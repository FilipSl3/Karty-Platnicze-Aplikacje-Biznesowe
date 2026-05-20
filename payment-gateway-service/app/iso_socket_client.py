from iso8583 import decode
from app.iso_spec import spec
import asyncio

HOST = "card-provider"
PORT = 9000


async def send_iso(raw_iso):

    reader, writer = await asyncio.open_connection(
        HOST,
        PORT
    )

    writer.write(bytes(raw_iso))
    await writer.drain()

    response = await reader.read(4096)

    writer.close()
    await writer.wait_closed()

    decoded, _ = decode(
        bytes(response),
        spec
    )

    return decoded
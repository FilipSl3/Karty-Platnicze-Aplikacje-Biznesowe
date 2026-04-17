# card-provider-service/app/main.py
import asyncio
import logging
from grpc import aio
from app import card_pb2_grpc
import app.card_pb2 as card_pb2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CardProviderServicer(card_pb2_grpc.CardProviderServicer):
    async def CreateCard(self, request, context):
        logger.info("Received CreateCard request")
        response = card_pb2.CreateCardResponse(
            card_token="test-token-123",
            masked_pan="**** **** **** 1234"
        )
        return response

async def serve():
    server = aio.server()
    card_pb2_grpc.add_CardProviderServicer_to_server(CardProviderServicer(), server)
    server.add_insecure_port('[::]:50051')
    logger.info("Card Provider Service RUNNING on port 50051 (gRPC)")
    await server.start()
    await server.wait_for_termination()

if __name__ == '__main__':
    asyncio.run(serve())

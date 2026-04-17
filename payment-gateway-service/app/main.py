# payment-gateway-service/app/main.py
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
import os
import grpc

# Importy wygenerowanych plików
from app import card_pb2, card_pb2_grpc

GRPC_URL = os.getenv("GRPC_SERVER_URL", "card-provider:50051")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Connecting to gRPC: {GRPC_URL}")
    yield


app = FastAPI(title="Payment Gateway", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "Payment Gateway", "status": "Running"}


@app.post("/test-connection")
async def test_grpc():
    try:
        # Tworzymy asynchroniczny kanał gRPC
        async with grpc.aio.insecure_channel(GRPC_URL) as channel:
            stub = card_pb2_grpc.CardProviderStub(channel)

            request = card_pb2.CreateCardRequest(
                user_id="test_user",
                account_id="test_acc",
                card_type="VIRTUAL"
            )

            response = await stub.CreateCard(request)
            return {"status": "Connection OK", "response": response.card_token}

    except Exception as e:
        return {"error": str(e)}

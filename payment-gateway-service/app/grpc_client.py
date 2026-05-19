import grpc

from app import card_pb2
from app import card_pb2_grpc

channel = grpc.aio.insecure_channel(
    "card-provider:50051"
)

stub = (
    card_pb2_grpc.CardProviderStub(
        channel
    )
)


async def authorize_transaction():

    response = (
        await stub.AuthorizeTransaction(
            card_pb2.AuthorizationRequest(
                card_token="tok_xxx",
                amount=300,
                currency="PLN",
                merchant_id="steam",
                merchant_name="Steam"
            )
        )
    )

    print(response)

    return response
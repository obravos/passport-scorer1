import hashlib
import logging
import random
import string
from typing import List, Optional, cast

from account.models import Account, AccountAPIKey, Community
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import ModelSchema, Schema
from ninja_extra import NinjaExtraAPI, status
from ninja_extra.exceptions import APIException
from ninja_jwt.authentication import JWTAuth
from ninja_jwt.schema import RefreshToken
from ninja_schema import Schema
from siwe import SiweMessage

log = logging.getLogger(__name__)

api = NinjaExtraAPI(urls_namespace="account")


class SiweVerifySubmit(Schema):
    message: dict
    signature: str


CHALLENGE_STATEMENT = "I authorize the passport scorer.\n\nnonce:"

# Returns a random username to be used in the challenge
def get_random_username():
    return "".join(random.choice(string.ascii_letters) for i in range(32))


# API endpoint for nonce
# TODO - give nonce an expiration time and store it to verify the user
@api.get("/nonce")
def nonce(request):
    return {
        "nonce": hashlib.sha256(
            str("".join(random.choice(string.ascii_letters) for i in range(32))).encode(
                "utf"
            )
        ).hexdigest()
    }


class TokenObtainPairOutSchema(Schema):
    refresh: str
    access: str
    # user: UserSchema


class UserSchema(Schema):
    first_name: str
    email: str


class MyTokenObtainPairOutSchema(Schema):
    refresh: str
    access: str
    user: UserSchema


class UnauthorizedException(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Unauthorized"


class ApiKeyDuplicateNameException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "An API Key with this name already exists"


class TooManyKeysException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "You have already created 5 API Keys"


class TooManyCommunitiesException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "You have already created 5 Communities"


class CommunityExistsException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "A community with this name already exists"


class CommunityHasNoNameException(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "A community must have a name"


class CommunityHasNoDescriptionException(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "A community must have a description"


class CommunityHasNoBodyException(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "A community must have a name and a description"


class SameCommunityNameException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "You've entered the same community name"


class SameCommunityDescriptionException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "You've entered the same community description"


class AccountApiSchema(ModelSchema):
    class Config:
        model = AccountAPIKey
        model_fields = ["name", "id", "prefix"]

    api_key: Optional[str] = None


class CommunityApiSchema(ModelSchema):
    class Config:
        model = Community
        model_fields = ["name", "description", "id"]


@api.post("/verify", response=TokenObtainPairOutSchema)
def submit_signed_challenge(request, payload: SiweVerifySubmit):

    payload.message["chain_id"] = payload.message["chainId"]
    payload.message["issued_at"] = payload.message["issuedAt"]
    message: SiweMessage = SiweMessage(payload.message)

    # TODO: wrap in try-catch
    is_valid_signature = message.verify(
        payload.signature
    )  # TODO: add more verification params

    message.json()
    address_lower = payload.message["address"]

    try:
        account = Account.objects.get(address=address_lower)
    except Account.DoesNotExist:
        user = get_user_model().objects.create_user(username=get_random_username())
        user.save()
        account = Account(address=address_lower, user=user)
        account.save()

    refresh = RefreshToken.for_user(account.user)
    refresh = cast(RefreshToken, refresh)

    return {"ok": True, "refresh": str(refresh), "access": str(refresh.access_token)}


class APIKeyName(Schema):
    name: str


@api.post("/api-key", auth=JWTAuth(), response=AccountApiSchema)
def create_api_key(request, payload: APIKeyName):
    try:
        account = request.user.account
        if AccountAPIKey.objects.filter(account=account).count() >= 5:
            raise TooManyKeysException()

        if AccountAPIKey.objects.filter(name=payload.name).count() == 1:
            raise ApiKeyDuplicateNameException()

        key_name = payload.name

        api_key, key = AccountAPIKey.objects.create_key(account=account, name=key_name)
    except Account.DoesNotExist:
        raise UnauthorizedException()

    return {
        "id": api_key.id,
        "name": api_key.name,
        "prefix": api_key.prefix,
        "api_key": key,
    }


@api.get("/api-key", auth=JWTAuth(), response=List[AccountApiSchema])
def get_api_keys(request):
    try:
        account = request.user.account
        api_keys = AccountAPIKey.objects.filter(account=account).all()

    except Account.DoesNotExist:
        raise UnauthorizedException()
    return api_keys


@api.delete("/api-key/{path:api_key_id}", auth=JWTAuth())
def delete_api_key(request, api_key_id):
    try:
        api_key = get_object_or_404(
            AccountAPIKey, id=api_key_id, account=request.user.account
        )
        api_key.delete()
    except Account.DoesNotExist:
        raise UnauthorizedException()
    return {"ok": True}


def health(request):
    return HttpResponse("Ok")


class CommunitiesPayload(Schema):
    name: str
    description: str


@api.post("/communities", auth=JWTAuth())
def create_community(request, payload: CommunitiesPayload):
    try:
        account = request.user.account
        if Community.objects.filter(account=account).count() >= 5:
            raise TooManyCommunitiesException()

        if Community.objects.filter(name=payload.name).count() == 1:
            raise CommunityExistsException()

        if len(payload.name) == 0:
            raise CommunityHasNoNameException()

        if len(payload.description) == 0:
            raise CommunityHasNoDescriptionException()

        if payload == None:
            raise CommunityHasNoBodyException()

        Community.objects.create(
            account=account, name=payload.name, description=payload.description
        )

    except Account.DoesNotExist:
        raise UnauthorizedException()

    return {"ok": True}


@api.get("/communities", auth=JWTAuth(), response=List[CommunityApiSchema])
def get_communities(request):
    try:
        account = request.user.account
        communities = Community.objects.filter(account=account).all()

    except Account.DoesNotExist:
        raise UnauthorizedException()
    return communities


class APIKeyId(Schema):
    id: str


@api.put("/communities/{community_id}", auth=JWTAuth())
def update_community(request, community_id, payload: CommunitiesPayload):
    try:
        community = get_object_or_404(
            Community, id=community_id, account=request.user.account
        )

        name = payload.name
        description = payload.description
        db_name = community.name
        db_description = community.description

        if len(name) == 0:
            raise CommunityHasNoNameException()

        if len(description) == 0:
            raise CommunityHasNoDescriptionException()

        if name == db_name:
            raise SameCommunityNameException()

        if Community.objects.filter(name=payload.name).count() == 1:
            raise CommunityExistsException()

        for attr, value in payload.dict().items():
            setattr(community, attr, value)

        community.save()
    except Account.DoesNotExist:
        raise UnauthorizedException()

    return {"ok": True}


@api.delete("/communities/{community_id}", auth=JWTAuth())
def delete_community(request, community_id):
    try:
        community = get_object_or_404(
            Community, id=community_id, account=request.user.account
        )
        community.delete()
    except Account.DoesNotExist:
        raise UnauthorizedException()
    return {"ok": True}


class ScorerResponse(Schema):
    ok: bool
    current_scorer: str
    choices: List[str]


@api.get("/communities/{community_id}/scorers", auth=JWTAuth(), response=ScorerResponse)
def get_community_scorers(request, community_id):
    try:
        community = get_object_or_404(
            Community, id=community_id, account=request.user.account
        )

        scorer = community.scorer
        current_scorer = scorer.type
        choices = [i[1] for i in scorer.Type.choices]
    except Community.DoesNotExist:
        raise UnauthorizedException()
    return {"ok": True, "current_scorer": current_scorer, "choices": choices}
